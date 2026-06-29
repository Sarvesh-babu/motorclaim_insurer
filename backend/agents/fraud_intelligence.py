from typing import Any
from datetime import datetime

import storage
from agents.base_agent import BaseAgent
from config import NPS_HIGH_DAYS, NPS_LOW_DAYS, NPS_MEDIUM_DAYS, SURVEYOR_THRESHOLD
from services.llm_client import ask_json
from services.pii import build_llm_safe_claim
from services.rag_client import get_fraud_kb_context

BASE_PROMPT = """You are a senior motor insurance fraud analyst with access to an institutional fraud knowledge base.
Review the claim data, visual fraud signals, damage findings, and ALL rule-based flags below.
Cross-reference with the fraud intelligence knowledge base to produce a calibrated fraud risk score.

{kb_context}

Claim details:
{claim_details}

Damage assessment findings (including visual fraud signals from image analysis):
{damage_findings}

Rule-based flags raised by system ({flag_count} flags):
{rule_flags}

Instructions:
- CRITICAL flags (vehicle mismatch, duplicate submission, pre-existing damage) must push score to 70+
- NEW POLICY SYNDROME flags carry risk-weighted scoring:
    HIGH RISK (0–{nps_high}d)   → contribute 25–35 pts to score
    MEDIUM RISK ({nps_high_1}–{nps_medium}d) → contribute 15–20 pts
    LOW RISK ({nps_medium_1}–{nps_low}d)  → contribute 5–10 pts
  Preserve the exact risk level label (HIGH/MEDIUM/LOW) in the indicators list.
- Use the fraud indicators and known scheme patterns from the Knowledge Base (if provided)
- Each flag should contribute to the score — more flags = higher score, not just the worst one
- The fraud_score should reflect ALL evidence: rule flags + visual signals + damage inconsistencies + KB patterns
- IMPORTANT — do NOT raise indicators for the following behaviours, as they are normal and responsible:
    * Submitting damage photos with the claim
    * Having a garage estimate or workshop name ready
    * Filing a complete, well-written description
    * Filing the claim within a few days of the incident
  These alone are NOT evidence of fraud. Only flag behaviour that directly contradicts the claim story
  or matches a known fraud scheme from the KB.
- Return ONLY valid JSON with no markdown fences or extra text

Return a JSON object with exactly these fields:
{{
  "fraud_score": <integer 0-100>,
  "fraud_label": "Low|Medium|High",
  "indicators": ["list of specific fraud indicators — preserve risk level labels exactly as given in flags"],
  "matched_schemes": ["any known fraud scheme names matched from KB"],
  "kb_references": ["indicator IDs or scheme IDs from KB that informed this score"],
  "policy_age_days": <integer — days between policy start and incident, -1 if unknown>,
  "nps_risk_level": "High|Medium|Low|None",
  "status": "completed",
  "summary": "Fraud Risk: X% (Label) | Top flag: <most significant indicator>"
}}
"""


def _policy_age_days(claim: dict) -> int:
    """Days between policy start and incident date."""
    try:
        policy = storage.get_policy_by_phone(claim.get("phone", ""))
        if not policy:
            return -1
        start_str = policy.get("policy_start", "")
        incident_str = claim.get("incident_date", "")
        if not start_str or not incident_str:
            return -1
        start = datetime.strptime(start_str[:10], "%Y-%m-%d").date()
        incident = datetime.strptime(incident_str[:10], "%Y-%m-%d").date()
        return (incident - start).days
    except Exception:
        return -1


# ── 24-check catalog ──────────────────────────────────────────────────────────
# The Fraud Intelligence agent runs 24 deterministic checks (FC-01…FC-24). Each
# is recorded as pass / flag / na. Only "flag" results feed the score and the
# LLM prompt; pass/na are surfaced in the UI Fraud Check List for transparency.
FRAUD_CHECK_NAMES: dict[str, str] = {
    "FC-01": "High-value / total-loss threshold",
    "FC-02": "Prior claims on policy (serial claimant)",
    "FC-03": "Policy validity at incident date",
    "FC-04": "New policy syndrome",
    "FC-05": "Reporting delay",
    "FC-06": "Night-time incident window",
    "FC-07": "Description language red flags",
    "FC-08": "Repeat-location pattern",
    "FC-09": "Claim clustering (30 days)",
    "FC-10": "Duplicate description",
    "FC-11": "Claim-to-sum-insured ratio",
    "FC-12": "Damage present in photos",
    "FC-13": "Vehicle identity match",
    "FC-14": "Pre-existing damage",
    "FC-15": "Registration plate verification",
    "FC-16": "Multiple vehicles in frame",
    "FC-17": "Workshop inflation (this claim)",
    "FC-18": "FIR present for theft / third-party",
    "FC-19": "Estimate uploaded without photos",
    "FC-20": "FIR consistency cross-check",
    "FC-21": "Telematics impact corroboration",
    "FC-22": "Telematics GPS location",
    "FC-23": "Garage under-declaration",
    "FC-24": "Cross-claim garage inflation",
    "FC-25": "Historical archive — garage fraud pattern",
    "FC-26": "Historical archive — claimant fraud pattern",
    "FC-27": "Historical archive — telematics fraud signature",
    "FC-28": "Visual forensics — AI-generated/manipulated evidence (image or video)",
}


def _run_fraud_checks(claim: dict, damage: dict, docs: dict | None = None) -> tuple[list[dict], dict]:
    """Run all 28 deterministic fraud checks. Returns (checks, layers):
    checks  — a list (in FC order) of {id, name, status: pass|flag|na, detail} dicts.
    layers  — {"historical_fraud_layer": {...}, "visual_forensics_layer": {...}}."""
    docs = docs or {}
    damage = damage or {}
    results: dict[str, dict] = {}

    def emit(cid: str, status: str, detail: str = ""):
        results[cid] = {"id": cid, "name": FRAUD_CHECK_NAMES[cid], "status": status, "detail": detail}

    # ── Shared precomputed values ─────────────────────────────────────────────
    claim_id = claim.get("claim_id", "")
    claim_type = (claim.get("claim_type") or "").lower()
    is_theft = "theft" in claim_type
    is_tp = "third party" in claim_type or "third-party" in claim_type
    desc_lower = (claim.get("description") or "").lower()
    incident_date = claim.get("incident_date", "")
    created_at = claim.get("created_at", "")
    policy = storage.get_policy_by_phone(claim.get("phone", ""))
    photos_present = bool(storage.get_claim_images(claim_id)) if claim_id else False

    all_claims = storage.get_all_claims()
    same_policy = [
        c for c in all_claims
        if c.get("policy_no") == claim.get("policy_no")
        and c.get("claim_id") != claim_id
    ]

    # ── FC-01: High-value / total-loss threshold ──────────────────────────────
    try:
        amount = float(claim.get("claim_amount") or 0)
        if amount <= 0:
            emit("FC-01", "na", "Claim amount not yet assessed.")
        elif amount > 500000:
            emit("FC-01", "flag", f"Claim amount ₹{amount:,.0f} exceeds ₹5,00,000 — potential total loss territory")
        elif amount > SURVEYOR_THRESHOLD:
            emit("FC-01", "flag",
                 f"Claim amount ₹{amount:,.0f} exceeds ₹{SURVEYOR_THRESHOLD:,} — mandatory surveyor required (IRDAI)")
        else:
            emit("FC-01", "pass", f"Claim amount ₹{amount:,.0f} within standard processing range.")
    except (ValueError, TypeError):
        emit("FC-01", "na", "Claim amount not parseable.")

    # ── FC-02: Prior claims on same policy ─────────────────────────────────────
    n_prior = len(same_policy)
    if n_prior == 0:
        emit("FC-02", "pass", "No prior claims on this policy.")
    elif n_prior == 1:
        emit("FC-02", "flag", "Policy has 1 prior claim — elevated frequency (FI-CLM-001)")
    else:
        emit("FC-02", "flag", f"Policy has {n_prior} prior claims — serial claimant pattern (FI-CLM-001)")

    # ── FC-03: Policy validity at incident date ────────────────────────────────
    if policy and policy.get("policy_end") and incident_date:
        try:
            policy_end = datetime.strptime(policy["policy_end"][:10], "%Y-%m-%d").date()
            incident_d = datetime.strptime(incident_date[:10], "%Y-%m-%d").date()
            if incident_d > policy_end:
                days_lapsed = (incident_d - policy_end).days
                emit("FC-03", "flag",
                     f"CRITICAL: Incident date ({incident_date[:10]}) is {days_lapsed} day(s) AFTER "
                     f"policy expiry ({policy['policy_end'][:10]}) — claim is NOT eligible for coverage (FI-POL-003)")
            else:
                emit("FC-03", "pass", "Incident date falls within the active policy period.")
        except Exception:
            emit("FC-03", "na", "Policy period dates not parseable.")
    else:
        emit("FC-03", "na", "Policy period unavailable.")

    # ── FC-04: New Policy Syndrome — tiered risk levels ────────────────────────
    age_days = _policy_age_days(claim)
    if age_days < 0:
        emit("FC-04", "na", "Policy start date unavailable.")
    elif 0 <= age_days <= NPS_HIGH_DAYS:
        emit("FC-04", "flag",
             f"NEW POLICY SYNDROME — HIGH RISK: Policy issued only {age_days} day(s) before "
             f"the incident (threshold: 0–{NPS_HIGH_DAYS} days). Strong indicator of "
             f"pre-existing damage fraud (FI-POL-001 / FS-001).")
    elif age_days <= NPS_MEDIUM_DAYS:
        emit("FC-04", "flag",
             f"NEW POLICY SYNDROME — MEDIUM RISK: Policy is {age_days} days old at incident "
             f"(threshold: {NPS_HIGH_DAYS + 1}–{NPS_MEDIUM_DAYS} days). Elevated scrutiny "
             f"required (FI-POL-001).")
    elif age_days <= NPS_LOW_DAYS:
        emit("FC-04", "flag",
             f"NEW POLICY SYNDROME — LOW RISK: Policy is {age_days} days old at incident "
             f"(threshold: {NPS_MEDIUM_DAYS + 1}–{NPS_LOW_DAYS} days). Minor elevated risk; "
             f"verify no pre-existing damage (FI-POL-001).")
    else:
        emit("FC-04", "pass", f"Policy is {age_days} days old at incident — outside the new-policy risk window.")

    # ── FC-05: Late reporting ──────────────────────────────────────────────────
    if created_at and incident_date:
        try:
            inc = datetime.strptime(incident_date[:10], "%Y-%m-%d").date()
            filed = datetime.strptime(created_at[:10], "%Y-%m-%d").date()
            delay = (filed - inc).days
            if delay > 7:
                emit("FC-05", "flag", f"Claim filed {delay} days after incident — late reporting (FI-CLM-003)")
            elif delay > 3:
                emit("FC-05", "flag", f"Claim filed {delay} days after incident — borderline late reporting")
            else:
                emit("FC-05", "pass", f"Claim filed {max(delay, 0)} day(s) after incident — timely reporting.")
        except Exception:
            emit("FC-05", "na", "Incident / filing dates not parseable.")
    else:
        emit("FC-05", "na", "Filing or incident date unavailable.")

    # ── FC-06: Night-time incident (22:00–05:00) ───────────────────────────────
    if incident_date and ("T" in incident_date or " " in incident_date):
        try:
            time_part = incident_date.replace("T", " ").split(" ")[1][:5]   # "23:30"
            hour = int(time_part.split(":")[0])
            if hour >= 22 or hour < 5:
                emit("FC-06", "flag",
                     f"Incident at {time_part} — night-time claim (22:00–05:00 high-risk window; "
                     f"reduced CCTV coverage, no witnesses likely)")
            else:
                emit("FC-06", "pass", f"Daytime incident ({time_part}).")
        except Exception:
            emit("FC-06", "na", "Incident time not parseable.")
    else:
        emit("FC-06", "na", "Incident time of day not provided.")

    # ── FC-07: Suspicious language in description ──────────────────────────────
    suspicious = [
        ("no witnesses",     "No witnesses mentioned — unverifiable incident (FI-LOC-001)"),
        ("no witness",       "No witnesses mentioned — unverifiable incident (FI-LOC-001)"),
        ("fled the scene",   "Hit-and-run: vehicle fled scene — third party unverifiable (FI-BEH-001)"),
        ("unknown vehicle",  "Unknown/unidentified third party vehicle — unverifiable (FI-BEH-001)"),
        ("empty road",       "Empty road claimed — no independent corroboration possible"),
        ("nobody around",    "No bystanders claimed — no independent corroboration possible"),
        ("parked",           "Vehicle was parked/unattended — no driver witness to impact"),
    ]
    if not desc_lower.strip():
        emit("FC-07", "na", "No description provided.")
    else:
        matched = next((label for phrase, label in suspicious if phrase in desc_lower), None)
        if matched:
            emit("FC-07", "flag", matched)
        else:
            emit("FC-07", "pass", "No high-risk language patterns in the description.")

    # ── FC-08: Same location as a prior claim on this policy ───────────────────
    if not same_policy:
        emit("FC-08", "na", "No prior claims to compare location against.")
    else:
        current_loc_words = set((claim.get("incident_location") or "").lower().split())
        hit = None
        for prev in same_policy:
            prev_loc_words = set((prev.get("incident_location") or "").lower().split())
            if len(current_loc_words & prev_loc_words) >= 3:
                hit = prev
                break
        if hit:
            emit("FC-08", "flag",
                 f"Incident location overlaps with prior claim {hit.get('claim_id')} "
                 f"— repeat-location fraud pattern (FI-LOC-002)")
        else:
            emit("FC-08", "pass", "Incident location does not overlap any prior claim on this policy.")

    # ── FC-09: Multiple claims within 30 days ──────────────────────────────────
    if not same_policy:
        emit("FC-09", "na", "No prior claims to compare timing against.")
    else:
        try:
            incident_d = datetime.strptime(incident_date[:10], "%Y-%m-%d").date()
            recent = []
            for c in same_policy:
                try:
                    prev_d = datetime.strptime(c.get("incident_date", "2000-01-01")[:10], "%Y-%m-%d").date()
                    if abs((prev_d - incident_d).days) <= 30:
                        recent.append(c)
                except Exception:
                    pass
            if recent:
                emit("FC-09", "flag",
                     f"{len(recent)} prior claim(s) on this policy within 30 days of this incident "
                     f"— high-frequency pattern (FI-CLM-002)")
            else:
                emit("FC-09", "pass", "No other claims on this policy within 30 days of the incident.")
        except Exception:
            emit("FC-09", "na", "Incident date not parseable.")

    # ── FC-10: Near-duplicate description (duplicate submission) ───────────────
    curr_words = set(desc_lower.split())
    if not same_policy or len(curr_words) <= 5:
        emit("FC-10", "na", "Not enough prior claims / description text to compare.")
    else:
        hit_ratio = None
        hit_id = None
        for prev in same_policy:
            prev_words = set((prev.get("description") or "").lower().split())
            if len(prev_words) > 5:
                ratio = len(curr_words & prev_words) / max(len(curr_words), len(prev_words))
                if ratio >= 0.70:
                    hit_ratio, hit_id = ratio, prev.get("claim_id")
                    break
        if hit_ratio is not None:
            emit("FC-10", "flag",
                 f"CRITICAL: Description is {int(hit_ratio*100)}% identical to prior claim "
                 f"{hit_id} — likely duplicate submission fraud")
        else:
            emit("FC-10", "pass", "Description is not a near-duplicate of any prior claim.")

    # ── FC-11: Claim amount as % of sum insured ────────────────────────────────
    try:
        sum_insured = float(policy.get("sum_insured", 0)) if policy else 0.0
        amount = float(claim.get("claim_amount") or 0)
        if sum_insured > 0 and amount > 0:
            pct = (amount / sum_insured) * 100
            if pct >= 75:
                emit("FC-11", "flag",
                     f"Claim amount ₹{amount:,.0f} is {pct:.0f}% of sum insured ₹{sum_insured:,.0f} "
                     f"— near total-loss; inflation or constructive total loss risk")
            elif pct >= 50:
                emit("FC-11", "flag",
                     f"Claim amount is {pct:.0f}% of sum insured — high-value claim requiring enhanced scrutiny")
            else:
                emit("FC-11", "pass", f"Claim amount is {pct:.0f}% of sum insured — within normal range.")
        else:
            emit("FC-11", "na", "Sum insured or claim amount unavailable.")
    except (ValueError, TypeError):
        emit("FC-11", "na", "Sum insured or claim amount not parseable.")

    # ── FC-12–16: Image-based visual fraud signals ─────────────────────────────
    if not photos_present:
        for cid in ("FC-12", "FC-13", "FC-14", "FC-15", "FC-16"):
            emit(cid, "na", "No photos submitted (documentation gap handled by image-quality gate).")
    else:
        # FC-12: No visible damage despite a claim being filed
        no_damage = damage.get("damage_present") is False or (
            isinstance(damage.get("damaged_parts"), list)
            and len(damage.get("damaged_parts")) == 0
            and damage.get("total_repair_estimate", {}).get("max", 0) in (0, None)
        )
        if no_damage:
            emit("FC-12", "flag",
                 "No visible damage detected in the submitted photos despite a claim being filed "
                 "— vehicle appears undamaged; possible invalid claim, wrong/old photos, or "
                 "pre-incident images (FI-DMG-002: Physics Mismatch)")
        else:
            emit("FC-12", "pass", "Visible damage detected in the submitted photos.")

        # FC-13: Vehicle identity match
        match = damage.get("vehicle_match_in_image")
        if match == "No":
            seen = damage.get("vehicle_seen_description", "unknown vehicle")
            emit("FC-13", "flag",
                 f"CRITICAL: Vehicle in images ({seen}) does NOT match registered vehicle "
                 f"— possible vehicle substitution fraud (FI-DMG-004)")
        elif match == "Unclear":
            emit("FC-13", "flag",
                 "Vehicle identity in images is unclear — make/model/plate not verifiable (FI-DMG-004)")
        elif match == "Yes":
            emit("FC-13", "pass", "Vehicle in images matches the registered vehicle.")
        else:
            emit("FC-13", "na", "No image-based vehicle identity check available.")

        # FC-14: Pre-existing damage
        if damage.get("pre_existing_damage_observed"):
            notes = damage.get("pre_existing_damage_notes") or "details not specified"
            emit("FC-14", "flag",
                 f"Pre-existing damage observed in images ({notes}) — "
                 f"old damage may be claimed as new incident (FI-DMG-001)")
        else:
            emit("FC-14", "pass", "No pre-existing damage observed in the submitted images.")

        # FC-15: Registration plate verification
        plate_visible = damage.get("registration_plate_visible")
        plate_seen = damage.get("plate_text_in_image", "")
        if plate_visible is False:
            emit("FC-15", "flag",
                 "Registration plate not visible in any submitted image — vehicle identity unverifiable (FI-DMG-003)")
        elif plate_visible and plate_seen:
            policy_vehicle = claim.get("vehicle", "")
            if plate_seen.upper().replace(" ", "") not in policy_vehicle.upper().replace(" ", ""):
                emit("FC-15", "flag",
                     f"Plate visible in image ({plate_seen}) may not match policy vehicle "
                     f"({policy_vehicle}) — verify registration")
            else:
                emit("FC-15", "pass", f"Registration plate visible ({plate_seen}) and consistent with policy vehicle.")
        elif plate_visible:
            emit("FC-15", "pass", "Registration plate visible in submitted images.")
        else:
            emit("FC-15", "na", "Plate visibility not assessed.")

        # FC-16: Multiple vehicles in frame
        if damage.get("multiple_vehicles_in_frame"):
            emit("FC-16", "flag",
                 "Multiple vehicles visible in claim images — possible staged collision scenario (FS-002)")
        else:
            emit("FC-16", "pass", "Single vehicle in frame — no staged-collision indicator.")

    # ── FC-17 / FC-23: Garage-vs-AI variance (inflation / under-declaration) ───
    garage_provided = damage.get("garage_estimate_provided")
    variance = damage.get("garage_vs_ai_variance_pct")
    garage_amt = damage.get("garage_estimate_amount_inr", 0) or 0
    if garage_provided and variance is not None:
        if damage.get("garage_inflation_flag"):
            emit("FC-17", "flag",
                 f"FI-INF-001: Workshop Inflation — Garage estimate ₹{garage_amt:,.0f} is "
                 f"{abs(variance):.0f}% above AI estimate (FS-004: Workshop Inflation Conspiracy)")
        elif variance > 25:
            emit("FC-17", "flag",
                 f"Garage estimate ₹{garage_amt:,.0f} is {variance:.0f}% above the assessed "
                 f"fair value — elevated estimate; verify line items (may or may not be fraud)")
        else:
            emit("FC-17", "pass",
                 f"Garage estimate ₹{garage_amt:,.0f} is within fair-value range of the AI estimate "
                 f"(variance {variance:+.0f}%).")

        if variance < -35:
            emit("FC-23", "flag",
                 f"Garage estimate ₹{garage_amt:,.0f} is {abs(variance):.0f}% below AI estimate "
                 f"— suspicious underdeclaration (possible cash settlement bypass)")
        else:
            emit("FC-23", "pass", "Garage estimate is not suspiciously below the AI assessment.")
    else:
        emit("FC-17", "na", "No garage estimate to compare against the AI assessment.")
        emit("FC-23", "na", "No garage estimate to compare against the AI assessment.")

    # ── FC-18: FIR present for theft / third-party ─────────────────────────────
    if not (is_theft or is_tp):
        emit("FC-18", "na", "Not a theft / third-party claim — FIR not mandatory.")
    elif not docs.get("fir"):
        emit("FC-18", "flag",
             f"No FIR uploaded for '{claim.get('claim_type')}' claim — "
             f"FIR is mandatory for theft and third-party incidents (FI-CLM-003)")
    else:
        emit("FC-18", "pass", "FIR uploaded for theft / third-party claim.")

    # ── FC-19: Garage estimate uploaded but no damage photos ───────────────────
    if not docs.get("estimate"):
        emit("FC-19", "na", "No garage estimate document uploaded.")
    elif not photos_present:
        emit("FC-19", "flag",
             "Garage estimate uploaded but no damage photos provided — "
             "possible prior-damage claim or cash settlement bypass")
    else:
        emit("FC-19", "pass", "Garage estimate uploaded alongside damage photos.")

    # ── FC-20: FIR consistency cross-check ─────────────────────────────────────
    parsed_fir = docs.get("fir")
    if not (parsed_fir and parsed_fir.get("parsed_ok")):
        emit("FC-20", "na", "No parseable FIR to cross-check.")
    else:
        mismatches: list[str] = []
        # Date mismatch
        fir_date = (parsed_fir.get("incident_date_in_fir") or "")[:10]
        claim_date = (claim.get("incident_date") or "")[:10]
        if fir_date and claim_date:
            try:
                diff = abs((datetime.strptime(fir_date, "%Y-%m-%d").date()
                            - datetime.strptime(claim_date, "%Y-%m-%d").date()).days)
                if diff > 2:
                    mismatches.append(f"FIR date ({fir_date}) differs from claim date ({claim_date}) by {diff} days")
            except Exception:
                pass
        # Location mismatch
        fir_loc = (parsed_fir.get("incident_location_in_fir") or "").lower()
        claim_loc = (claim.get("incident_location") or "").lower()
        if fir_loc and claim_loc:
            stopwords = {"the", "a", "an", "and", "in", "of", "at", "on", "road", "street", "near"}
            fir_words = set(fir_loc.split()) - stopwords
            claim_words = set(claim_loc.split()) - stopwords
            if fir_words and claim_words and not fir_words & claim_words:
                mismatches.append(
                    f"FIR location '{parsed_fir.get('incident_location_in_fir')}' "
                    f"does not match claimed location '{claim.get('incident_location')}'")
        # Vehicle registration mismatch
        fir_reg = (parsed_fir.get("vehicle_reg_in_fir") or "").upper().replace(" ", "").replace("-", "")
        policy_vehicle = (claim.get("vehicle") or "").upper().replace(" ", "").replace("-", "")
        if fir_reg and fir_reg not in policy_vehicle:
            mismatches.append(
                f"FIR vehicle reg '{parsed_fir.get('vehicle_reg_in_fir')}' does not match policy vehicle")
        if mismatches:
            emit("FC-20", "flag", "FI-LOC-001: FIR mismatch — " + "; ".join(mismatches) + " — story inconsistency")
        else:
            emit("FC-20", "pass", "FIR date, location and vehicle registration are consistent with the claim.")

    # ── FC-21 / FC-22: Telematics/IoT cross-checks ─────────────────────────────
    telematics = docs.get("telematics")
    gps_check: dict | None = None
    if not (telematics and telematics.get("parsed_ok")):
        emit("FC-21", "na", "No telematics data available.")
        emit("FC-22", "na", "No telematics data available.")
    else:
        # FC-21: impact corroboration
        if is_theft:
            emit("FC-21", "na", "Theft claim — collision telematics not applicable.")
        elif telematics.get("impact_g_force") is None:
            emit("FC-21", "na", "Telematics lacks impact-force data.")
        elif telematics.get("hard_braking_detected") is False and telematics["impact_g_force"] < 1.0:
            emit("FC-21", "flag",
                 f"Telematics shows no hard-braking or impact event (impact "
                 f"{telematics['impact_g_force']}g) despite a claimed collision — physics mismatch (FI-DMG-002 style)")
        else:
            emit("FC-21", "pass", "Telematics impact signature is consistent with a collision.")

        # FC-22: GPS location
        if not telematics.get("gps_trail"):
            emit("FC-22", "na", "No GPS trail in telematics.")
        else:
            try:
                from agents.context_verification import _geocode, _check_gps_trail
                geo = _geocode(claim.get("incident_location", ""))
                gps_check = _check_gps_trail(telematics, geo)
                match = gps_check["gps_location_match"]
                if match is False:
                    emit("FC-22", "flag",
                         f"CRITICAL: Telematics GPS trail is {gps_check['gps_distance_km']}km from "
                         f"the claimed incident location — vehicle was not where the claim says it was")
                elif match is True:
                    emit("FC-22", "pass", "Telematics GPS trail matches the claimed incident location.")
                else:
                    # match is None — claimed location could not be geocoded, so the
                    # trail can't be verified either way. Not a pass.
                    emit("FC-22", "na", "Claimed location could not be geocoded — GPS trail not verified.")
            except Exception:
                emit("FC-22", "na", "GPS trail could not be verified against the claimed location.")

    # ── FC-24: Cross-claim garage inflation (FS-004) ───────────────────────────
    try:
        from services.garage_intel import cross_claim_inflation
        gc = cross_claim_inflation(claim)
        emit("FC-24", gc["status"], gc["detail"])
    except Exception:
        emit("FC-24", "na", "Cross-claim garage history unavailable.")

    # ── FC-25 / FC-26 / FC-27: Historical fraud archive cross-checks ───────────
    # Distinct from FC-24 (which scans the LIVE operational queue): this archive
    # is a standing knowledge base, so these checks still have history to
    # compare against even when the live queue has just been reset/is empty.
    from services.historical_fraud_intel import build_historical_fraud_layer
    historical_layer = build_historical_fraud_layer(claim, telematics, gps_check)
    emit("FC-25", historical_layer["garage_history"]["status"], historical_layer["garage_history"]["detail"])
    emit("FC-26", historical_layer["claimant_history"]["status"], historical_layer["claimant_history"]["detail"])
    emit("FC-27", historical_layer["telematics_history"]["status"], historical_layer["telematics_history"]["detail"])

    # ── FC-28: Visual forensics — AI-generated/manipulated evidence ───────────
    # Combines the image-side signal (vision LLM authenticity_flags + EXIF +
    # local AI-image-detector, all already computed by damage_assessment) with
    # the video-side signal (the same local detector run on dashcam frames).
    img_auth = (damage.get("image_quality") or {}).get("authenticity") or {}
    video_signals = docs.get("dashcam_forensics") or []
    video_suspects = [s for s in video_signals if s.get("is_ai_generated_suspected")]
    visual_forensics_layer = {
        "image": img_auth,
        "video": {"signals": video_signals, "suspect_count": len(video_suspects)},
    }
    image_suspect = bool(img_auth.get("review_recommended"))
    if image_suspect or video_suspects:
        parts = []
        if image_suspect:
            parts.append("damage photo(s) flagged by the vision model/EXIF/local detector")
        if video_suspects:
            parts.append(f"{len(video_suspects)} dashcam frame(s) flagged by the local AI-image detector")
        emit("FC-28", "flag", "Visual forensics: " + "; ".join(parts) + " — possible synthetic/manipulated evidence.")
    elif video_signals or img_auth:
        emit("FC-28", "pass", "No AI-generated/manipulated signals detected in images or dashcam frames.")
    else:
        emit("FC-28", "na", "No images or dashcam frames available for visual forensics.")

    # Return in FC order, defaulting any unexpectedly-missing check to na
    checks = [
        results.get(cid, {"id": cid, "name": name, "status": "na", "detail": "Not evaluated."})
        for cid, name in FRAUD_CHECK_NAMES.items()
    ]
    layers = {"historical_fraud_layer": historical_layer, "visual_forensics_layer": visual_forensics_layer}
    return checks, layers


class FraudIntelligenceAgent(BaseAgent):
    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        claim = context["claim"]
        damage = context["agents"].get("damage_assessment", {})
        docs = context.get("docs", {})
        checks, layers = _run_fraud_checks(claim, damage, docs)
        flags = [c["detail"] for c in checks if c["status"] == "flag"]

        # Build policy age hint for RAG query
        age_days = _policy_age_days(claim)
        age_hint = f"policy age {age_days} days" if age_days >= 0 else ""

        # RAG: fetch relevant fraud indicators and schemes
        description = claim.get("description", "")
        kb_context = get_fraud_kb_context(description, age_hint)

        claim_details = "\n".join(f"{k}: {v}" for k, v in build_llm_safe_claim(claim).items())
        damage_str = str(damage)
        flags_str = "\n".join(f"- {f}" for f in flags) if flags else "None"

        prompt = BASE_PROMPT.format(
            kb_context=kb_context,
            claim_details=claim_details,
            damage_findings=damage_str,
            rule_flags=flags_str,
            flag_count=len(flags),
            nps_high=NPS_HIGH_DAYS,
            nps_high_1=NPS_HIGH_DAYS + 1,
            nps_medium=NPS_MEDIUM_DAYS,
            nps_medium_1=NPS_MEDIUM_DAYS + 1,
            nps_low=NPS_LOW_DAYS,
        )
        # ── Deterministic base score from rule flags ──────────────────────────
        # Each CRITICAL flag contributes 35 pts; historical-archive matches (an
        # exact prior-fraud-case hit on garage/claimant/telematics signature, or
        # a synthetic-image detection) contribute 30 pts — strong corroborating
        # evidence, just under CRITICAL; each regular flag contributes 8 pts;
        # NPS flags use their tier weights. The LLM may only move the final
        # score within ±15 of this base — preventing run-to-run variance on
        # identical evidence.
        _HIGH_WEIGHT_CHECKS = {"FC-25", "FC-26", "FC-27", "FC-28"}
        base_score = 0
        for c in checks:
            if c["status"] != "flag":
                continue
            f = c["detail"]
            fu = f.upper()
            if "CRITICAL" in fu:
                base_score += 35
            elif c["id"] in _HIGH_WEIGHT_CHECKS:
                base_score += 30
            elif "NEW POLICY SYNDROME — HIGH" in fu:
                base_score += 30
            elif "NEW POLICY SYNDROME — MEDIUM" in fu:
                base_score += 17
            elif "NEW POLICY SYNDROME — LOW" in fu:
                base_score += 7
            else:
                base_score += 8
        base_score = min(base_score, 95)

        result = ask_json(prompt, agent_name="fraud_intelligence", claim_id=claim.get("claim_id"))
        result.setdefault("status", "completed")

        # Clamp the LLM score to base ± 15 to keep it reproducible
        llm_score = int(result.get("fraud_score") or 0)
        clamped = max(base_score - 15, min(base_score + 15, llm_score))
        clamped = max(0, min(100, clamped))
        result["fraud_score"] = clamped
        result["fraud_score_base"] = base_score   # expose for transparency

        # Derive the label from the final score so it always matches the decision
        # bands (Approve <40 / Escalate 40–69 / Reject 70+). The LLM sometimes
        # mislabels (e.g. tags a 47 as "High"); Python is authoritative.
        if clamped >= 70:
            result["fraud_label"] = "High"
        elif clamped >= 40:
            result["fraud_label"] = "Medium"
        else:
            result["fraud_label"] = "Low"

        # Always populate NPS fields authoritatively from Python (not LLM)
        result["policy_age_days"] = age_days
        if 0 <= age_days <= NPS_HIGH_DAYS:
            result["nps_risk_level"] = "High"
        elif 0 <= age_days <= NPS_MEDIUM_DAYS:
            result["nps_risk_level"] = "Medium"
        elif 0 <= age_days <= NPS_LOW_DAYS:
            result["nps_risk_level"] = "Low"
        else:
            result["nps_risk_level"] = "None"

        # Strip any LLM-invented NPS indicators when Python says no flag applies.
        # The LLM sometimes generates NPS indicators from its training knowledge
        # even when no NPS rule flag was passed. Python is authoritative here.
        if result["nps_risk_level"] == "None":
            result["indicators"] = [
                ind for ind in (result.get("indicators") or [])
                if "NEW POLICY SYNDROME" not in ind.upper()
                and "NEW POLICY" not in ind.upper()
            ]

        # Expose the full 28-check list for the UI Fraud Check List panel.
        result["fraud_checks"] = checks
        flagged = sum(1 for c in checks if c["status"] == "flag")
        passed  = sum(1 for c in checks if c["status"] == "pass")
        na      = sum(1 for c in checks if c["status"] == "na")
        result["fraud_check_counts"] = {"flagged": flagged, "passed": passed, "na": na}

        # Two layered outputs: image/video forensics, and historical cross-claim
        # fraud patterns (same garage / same claimant / same telematics signature
        # seen in past fraud cases) — see services/historical_fraud_intel.py.
        result["visual_forensics_layer"] = layers["visual_forensics_layer"]
        result["historical_fraud_layer"] = layers["historical_fraud_layer"]

        # Expand bare KB codes (FI-xxx / FS-xxx) into {title, description,
        # relevance} so the PDF report and frontend don't show raw codes —
        # relevance is pulled from this claim's own flagged checks where possible.
        from services.kb_glossary import expand_list
        result["kb_references_expanded"] = expand_list(result.get("kb_references"), checks)
        result["matched_schemes_expanded"] = expand_list(result.get("matched_schemes"), checks)

        # Regenerate the summary from the Python-authoritative score and label so
        # the collapsed agent row never shows a stale LLM-generated figure. Pick
        # the most significant deterministic flag as the headline (CRITICAL first).
        top_flag = next((f for f in flags if "CRITICAL" in f.upper()), flags[0] if flags else None)
        result["summary"] = (
            f"Fraud Risk: {clamped}% ({result['fraud_label']}) | "
            f"{flagged} of {len(checks)} checks flagged"
            + (f" | Top: {top_flag}" if top_flag else " | No deterministic flags raised")
        )

        return result
