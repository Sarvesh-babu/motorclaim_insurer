import asyncio
from typing import Any

import storage
from config import SURVEYOR_THRESHOLD


async def run_pipeline(claim_id: str, claim: dict, queue: asyncio.Queue):
    """
    Sequences all 5 agents, emits SSE events per agent, writes result.json.
    Agents are imported lazily so Phase 1 works without them implemented.
    """
    from agents.damage_assessment import DamageAssessmentAgent
    from agents.fraud_intelligence import FraudIntelligenceAgent
    from agents.incident_reconstruction import IncidentReconstructionAgent
    from agents.context_verification import ContextVerificationAgent
    from agents.settlement_recommendation import SettlementRecommendationAgent

    agent_sequence = [
        ("damage_assessment", DamageAssessmentAgent()),
        ("fraud_intelligence", FraudIntelligenceAgent()),
        ("incident_reconstruction", IncidentReconstructionAgent()),
        ("context_verification", ContextVerificationAgent()),
        ("settlement_recommendation", SettlementRecommendationAgent()),
    ]

    claim_type_lower = (claim.get("claim_type") or "").lower()
    is_theft = "theft" in claim_type_lower
    is_tp    = "third party" in claim_type_lower or "third-party" in claim_type_lower

    context: dict[str, Any] = {
        "claim": claim,
        "agents": {},
        "claim_type_flags": {
            "is_theft": is_theft,
            "is_third_party": is_tp,
            "is_own_damage": not is_theft and not is_tp,
        },
    }

    # Parse any uploaded supporting documents (estimate / FIR) before agents run
    try:
        from services.document_parser import parse_document
        claim_docs = storage.get_claim_docs(claim_id)
        docs_context: dict[str, Any] = {}
        for doc_type in ("estimate", "fir"):
            paths = claim_docs.get(doc_type, [])
            if paths:
                docs_context[doc_type] = parse_document(claim_id, paths[0], doc_type, claim)
            else:
                docs_context[doc_type] = None
        context["docs"] = docs_context
    except Exception:
        context["docs"] = {"estimate": None, "fir": None}

    # Dash cam video → frame extraction (Groq vision has no video input type,
    # so a clip is converted to still frames that flow through the existing
    # image pipeline) and telematics/IoT data parsing — both optional evidence.
    try:
        from services.video_parser import extract_frames
        dashcam_paths = claim_docs.get("dashcam", [])
        context["docs"]["dashcam_frames"] = (
            extract_frames(claim_id, dashcam_paths[0]) if dashcam_paths else []
        )
    except Exception:
        context["docs"]["dashcam_frames"] = []

    # Video-side forensics — the same local AI-image detector damage_assessment
    # runs on damage photos, run here on dashcam frames too, so fraud_intelligence
    # has a video-based signal independent of incident_reconstruction's narrative
    # (the two now run in parallel — see the agent loop below).
    try:
        from services import forensics_detector
        context["docs"]["dashcam_forensics"] = forensics_detector.check_images(
            context["docs"]["dashcam_frames"]
        )
    except Exception:
        context["docs"]["dashcam_forensics"] = []

    try:
        from services.telematics_parser import parse_telematics, save_parsed_telematics
        telematics_paths = claim_docs.get("telematics", [])
        if telematics_paths:
            telematics_result = parse_telematics(telematics_paths[0])
            save_parsed_telematics(claim_id, telematics_result)
        else:
            telematics_result = None
        context["docs"]["telematics"] = telematics_result
    except Exception:
        context["docs"]["telematics"] = None

    storage.update_claim_field(claim_id, "status", "Under Investigation")
    # Clear any previous adjuster decision so a re-investigation starts clean.
    # Notes are preserved (they remain useful context for the new adjudicator).
    prev = storage.get_result(claim_id) or {}
    if prev.get("adjuster_decision"):
        prev.pop("adjuster_decision", None)
        storage.save_result(claim_id, prev)

    flags = context["claim_type_flags"]

    async def run_one_agent(agent_name: str, agent) -> None:
        """Runs a single agent, applies claim-type stubs, persists the result,
        and emits its SSE events. Safe to run several of these concurrently via
        asyncio.gather — each only writes to its own context["agents"][name] key
        and storage.update_agent_result() does a single synchronous read-modify-
        write with no `await` inside it, so there's no interleaving hazard even
        though the underlying agent.run() calls execute in separate threads."""
        await queue.put({"type": "agent_start", "agent": agent_name})
        try:
            # ── Claim-type branching ───────────────────────────────────────────
            # Theft: no own-vehicle damage photos, settlement = IDV payout.
            # Third-party: own-vehicle damage may exist but liability is separate.
            # Skip/stub agents that don't apply to the claim type.
            if agent_name == "damage_assessment" and flags["is_theft"]:
                result = {
                    "status": "completed",
                    "damage_present": False,
                    "no_damage_reason": "Theft claim — no own-vehicle damage photos expected.",
                    "damaged_parts": [],
                    "overall_severity": "None",
                    "total_repair_estimate": {"min": 0, "max": 0},
                    "consistent_with_description": "High",
                    "notes": "Theft claim: damage assessment not applicable. Settlement will be based on IDV.",
                    "vehicle_match_in_image": "Unclear",
                    "image_quality": {"gate_passed": True, "issues": [], "photo_count": 0},
                    "summary": "Theft claim — IDV settlement path",
                }
            elif agent_name == "incident_reconstruction" and flags["is_theft"]:
                result = {
                    "status": "completed",
                    "collision_type": "Theft",
                    "damage_matches_story": True,
                    "confidence": 80,
                    "reconstruction": "Vehicle reported stolen. No collision reconstruction applicable.",
                    "reconstruction_bullets": [
                        "Claim type is theft — no impact damage to reconstruct.",
                        "FIR verification is the primary evidence check.",
                        "Story consistency depends on FIR match, not visual damage.",
                    ],
                    "inconsistencies": [],
                    "summary": "Theft — no reconstruction applicable | Confidence: 80%",
                }
            else:
                result = await asyncio.to_thread(agent.run, context)

            context["agents"][agent_name] = result
            storage.update_agent_result(claim_id, agent_name, result)
            # After damage assessment, write the AI-estimated amount back to CSV
            if agent_name == "damage_assessment":
                est = result.get("total_repair_estimate", {})
                # Use mid-point so downstream agents see a representative value
                lo = float(est.get("min", 0) if isinstance(est, dict) else 0)
                hi = float(est.get("max", 0) if isinstance(est, dict) else 0)
                amount = int((lo + hi) / 2) if hi > 0 else int(lo)
                storage.update_claim_field(claim_id, "claim_amount", str(amount))
                # Also push into in-memory context so fraud flags (A2) and
                # settlement (A5) read the real estimate, not the stale 0.
                context["claim"]["claim_amount"] = amount
            await queue.put({"type": "agent_done", "agent": agent_name, "result": result})
        except Exception as exc:
            error_data = {"status": "error", "error": str(exc)}
            context["agents"][agent_name] = error_data
            storage.update_agent_result(claim_id, agent_name, error_data)
            await queue.put({"type": "agent_error", "agent": agent_name, "error": str(exc)})

    by_name = dict(agent_sequence)

    # Phase 1: damage_assessment alone — fraud_intelligence and incident_reconstruction
    # both only depend on its output, not on each other.
    await run_one_agent("damage_assessment", by_name["damage_assessment"])

    # Phase 2: fraud_intelligence and incident_reconstruction run IN PARALLEL.
    # Fraud's image/video forensics layer is built from dedicated deterministic
    # signals (vision authenticity flags, EXIF, the local AI-image detector run
    # on both damage photos and dashcam frames) computed independently of
    # incident_reconstruction's narrative — so neither agent needs to wait on
    # the other's output.
    await asyncio.gather(
        run_one_agent("fraud_intelligence", by_name["fraud_intelligence"]),
        run_one_agent("incident_reconstruction", by_name["incident_reconstruction"]),
    )

    # Phase 3: context_verification and settlement_recommendation — settlement
    # needs both Phase 2 outputs, so this stays sequential.
    await run_one_agent("context_verification", by_name["context_verification"])
    await run_one_agent("settlement_recommendation", by_name["settlement_recommendation"])

    # Build summary from settlement agent output
    settlement     = context["agents"].get("settlement_recommendation", {})
    fraud          = context["agents"].get("fraud_intelligence", {})
    damage         = context["agents"].get("damage_assessment", {})
    reconstruction = context["agents"].get("incident_reconstruction", {})
    context_v      = context["agents"].get("context_verification", {})

    # If Agent 5 errored, mark investigation failed rather than misleadingly "Completed"
    agent5_errored = settlement.get("status") == "error"

    # Derive incident_consistency from reconstruction agent
    matches = reconstruction.get("damage_matches_story", None)
    conf    = reconstruction.get("confidence", 0) or 0
    if matches is True:
        incident_consistency = "High" if conf >= 70 else "Medium"
    elif matches is False:
        incident_consistency = "Low"
    else:
        incident_consistency = "Unknown"

    # Derive external_verification from context agent
    loc_verified = context_v.get("location_verified", None)
    if loc_verified is True:
        external_verification = "High"
    elif loc_verified is False:
        external_verification = "Low"
    else:
        external_verification = "Unknown"

    # Derive behavioural_analysis from fraud label
    fraud_label = fraud.get("fraud_label", "Unknown")
    behavioural_analysis = {
        "Low": "Low Risk", "Medium": "Medium Risk", "High": "High Risk"
    }.get(fraud_label, "Unknown")

    # ── Hard eligibility guard (policy expiry / pre-inception) ───────────────
    # Coverage is a binary fact — if the incident is outside the policy period,
    # no LLM reasoning can override it. Check before trusting the LLM decision.
    eligibility_fail = context_v.get("policy_coverage_note", "")
    is_ineligible = any(
        kw in eligibility_fail.upper()
        for kw in ("POLICY EXPIRED", "PRE-INCEPTION", "NOT ELIGIBLE")
    )

    decision = settlement.get("decision", "Pending")
    if is_ineligible:
        decision = "Reject"

    # ── Hard consistency guard (story mismatch) ───────────────────────────────
    # If incident reconstruction found the visible damage does NOT match the
    # claimant's account, the claim must not be auto-approved. A story mismatch
    # is a material validity/fraud signal that needs a human adjudicator. Like
    # the eligibility guard above, this deterministically overrides an LLM
    # "Approve" — the model only recommends; a hard mismatch wins.
    story_mismatch = reconstruction.get("damage_matches_story") is False
    story_mismatch_blocked = decision == "Approve" and story_mismatch
    if story_mismatch_blocked:
        decision = "Escalate"
        settlement["recommended_settlement"] = 0
        prior = settlement.get("decision_reasons") or []
        settlement["decision_reasons"] = [
            "Auto-approval blocked: incident reconstruction found the visible damage "
            "does not match the claimant's account (story mismatch). Escalated for human review.",
            *prior,
        ]

    # ── #2 Settlement breakdown (transparent, deterministic math) ─────────────
    breakdown = None
    try:
        from services.settlement_calc import compute_settlement_breakdown
        policy = storage.get_policy_by_phone(claim.get("phone", ""))
        breakdown = compute_settlement_breakdown(claim, policy, damage, decision)
    except Exception:
        breakdown = None

    # Reconcile the headline figure with the breakdown on approval so the
    # number shown matches the visible line-item math.
    recommended = settlement.get("recommended_settlement", 0)
    if breakdown and decision == "Approve":
        recommended = breakdown["net_payable"]

    if breakdown:
        settlement["settlement_breakdown"] = breakdown
        settlement["recommended_settlement"] = recommended
        storage.update_agent_result(claim_id, "settlement_recommendation", settlement)

    # ── #6 Confidence-based routing + image quality gate ──────────────────────
    CONF_THRESHOLD = 60
    routing_reasons: list[str] = []

    for label, conf in [
        ("Incident Reconstruction", reconstruction.get("confidence")),
        ("Settlement Recommendation", settlement.get("confidence")),
    ]:
        if isinstance(conf, (int, float)) and conf < CONF_THRESHOLD:
            routing_reasons.append(f"{label} confidence {int(conf)}% is below the {CONF_THRESHOLD}% threshold")

    img_q = damage.get("image_quality") or {}
    if img_q and not img_q.get("gate_passed", True):
        issues = "; ".join(img_q.get("issues", [])) or "image quality insufficient"
        routing_reasons.append(f"Image quality gate not passed: {issues}")

    if story_mismatch_blocked:
        routing_reasons.append(
            "Incident reconstruction: visible damage does not match the claimant's "
            "account (story mismatch) — auto-approval blocked, needs human review"
        )

    needs_human_review = bool(routing_reasons)

    summary = {
        "fraud_risk_score":    fraud.get("fraud_score", 0),
        "fraud_risk_label":    fraud_label,
        "damage_consistency":  damage.get("consistent_with_description", "Unknown"),
        "incident_consistency": incident_consistency,
        "external_verification": external_verification,
        "behavioural_analysis": behavioural_analysis,
        "overall_confidence":  settlement.get("confidence", 0),
        "decision":            decision,
        "recommended_settlement": recommended,
        "settlement_breakdown": breakdown,
        "needs_human_review":  needs_human_review,
        "routing_reasons":     routing_reasons,
        "image_quality":       img_q or None,
    }

    result_doc = storage.get_result(claim_id) or {"claim_id": claim_id, "agents": {}, "summary": {}}
    result_doc["summary"] = summary
    storage.save_result(claim_id, result_doc)

    # ── Map decision + routing to workflow status (lifecycle) ─────────────────
    updated_claim = storage.get_claim(claim_id) or {}
    final_amount = float(updated_claim.get("claim_amount") or 0)
    images = storage.get_claim_images(claim_id)
    claim_docs = storage.get_claim_docs(claim_id)
    has_estimate_only = bool(claim_docs.get("estimate")) and not images

    if agent5_errored:
        final_status = "Investigation Error"
    elif needs_human_review:
        final_status = "Pending Review"
    elif decision == "Escalate" and (final_amount > SURVEYOR_THRESHOLD or has_estimate_only):
        final_status = "Survey Required"
    else:
        final_status = {
            "Approve": "Approved",
            "Reject": "Rejected",
            "Escalate": "Escalated",
        }.get(decision, "Completed")

    storage.update_claim_field(claim_id, "status", final_status)

    await queue.put({"type": "done", "summary": summary})
