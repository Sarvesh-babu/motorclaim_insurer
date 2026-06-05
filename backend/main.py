import asyncio
import json
import os
from datetime import datetime
from typing import AsyncGenerator

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import storage
from config import CLAIMS_DIR

app = FastAPI(title="ClaimIntel API", version="1.0.0")

_cors_raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
_CORS_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/uploads",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "data", "claims")),
    name="uploads",
)

_sse_queues: dict[str, asyncio.Queue] = {}


def get_sse_queue(claim_id: str) -> asyncio.Queue:
    if claim_id not in _sse_queues:
        _sse_queues[claim_id] = asyncio.Queue()
    return _sse_queues[claim_id]


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ClaimIntel"}


# ── KB Status ─────────────────────────────────────────────────────────────────

@app.get("/api/kb/status")
def kb_status():
    """
    Returns whether the ChromaDB vectorstore has been built and lists
    each collection with its vector count.
    Response: { built: bool, collections: [{name, count}] }
    """
    try:
        import chromadb
        from config import VECTORSTORE_DIR
        if not os.path.isdir(VECTORSTORE_DIR):
            return {"built": False, "collections": []}
        client = chromadb.PersistentClient(path=VECTORSTORE_DIR)
        colls = client.list_collections()
        collections = []
        for c in colls:
            col = client.get_collection(c.name)
            collections.append({"name": c.name, "count": col.count()})
        return {"built": len(collections) > 0, "collections": collections}
    except Exception as e:
        return {"built": False, "collections": [], "error": str(e)}


# ── Customer / Policy Lookup ──────────────────────────────────────────────────

@app.get("/api/customers/lookup")
def lookup_customer(phone: str = Query(...)):
    policy = storage.get_policy_by_phone(phone)
    if not policy:
        raise HTTPException(status_code=404, detail="No active policy found for this phone number")
    return policy


# ── Claims ────────────────────────────────────────────────────────────────────

@app.post("/api/claims/", status_code=201)
def create_claim(
    policy_no: str = Form(...),
    claimant: str = Form(...),
    phone: str = Form(...),
    vehicle: str = Form(...),
    claim_type: str = Form("Motor - Own Damage"),
    incident_date: str = Form(...),
    incident_location: str = Form(...),
    description: str = Form(...),
    garage_estimate_amount: str = Form(""),
    garage_workshop_name: str = Form(""),
    fir_number: str = Form(""),
):
    # Reject future incident dates
    try:
        incident_dt = datetime.fromisoformat(incident_date.replace("T", " ").split("+")[0])
        if incident_dt > datetime.now():
            raise HTTPException(status_code=422, detail="Incident date cannot be in the future")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid incident date format")

    claim_id = storage.generate_claim_id()
    claim_data = {
        "claim_id": claim_id,
        "policy_no": policy_no,
        "claimant": claimant,
        "phone": phone,
        "vehicle": vehicle,
        "claim_type": claim_type,
        "incident_date": incident_date,
        "incident_location": incident_location,
        "claim_amount": 0,
        "description": description,
        "status": "Pending",
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "garage_estimate_amount": garage_estimate_amount or "",
        "garage_workshop_name": garage_workshop_name or "",
        "fir_number": fir_number or "",
    }
    storage.save_claim(claim_data)
    os.makedirs(os.path.join(CLAIMS_DIR, claim_id, "images"), exist_ok=True)
    storage.save_result(claim_id, {"claim_id": claim_id, "agents": {}, "summary": {}})
    return claim_data


@app.get("/api/claims/")
def list_claims():
    claims = storage.get_all_claims()
    for c in claims:
        result = storage.get_result(c["claim_id"])
        c["decision"] = result["summary"].get("decision", "") if result and result.get("summary") else ""
    return claims


@app.get("/api/claims/{claim_id}")
def get_claim(claim_id: str):
    claim = storage.get_claim(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    result = storage.get_result(claim_id)
    policy = storage.get_policy_by_phone(claim.get("phone", ""))

    # Lazy backfill: compute settlement breakdown for claims investigated before
    # the transparent-math feature existed, so older/seed claims still show it.
    # Also upgrade breakdowns that predate the fair-value check (no
    # `assessed_fair_value` field) so existing claims gain the benchmark audit
    # without a full re-investigation.
    try:
        summary = (result or {}).get("summary") or {}
        existing_bd = summary.get("settlement_breakdown") or {}
        needs_bd = not existing_bd
        needs_upgrade = bool(existing_bd) and "assessed_fair_value" not in existing_bd
        if summary.get("decision") and (needs_bd or needs_upgrade):
            from services.settlement_calc import compute_settlement_breakdown
            damage = (result.get("agents") or {}).get("damage_assessment", {})
            bd = compute_settlement_breakdown(claim, policy, damage, summary["decision"])
            if bd:
                summary["settlement_breakdown"] = bd
    except Exception:
        pass

    return {"claim": claim, "result": result, "policy": policy}


# ── Image List ────────────────────────────────────────────────────────────────

@app.get("/api/claims/{claim_id}/images")
def list_images(claim_id: str):
    paths = storage.get_claim_images(claim_id)
    urls = [f"/uploads/{claim_id}/images/{os.path.basename(p)}" for p in paths]
    return {"images": urls}


# ── Image Delete ─────────────────────────────────────────────────────────────

@app.delete("/api/claims/{claim_id}/images/{filename}")
def delete_image(claim_id: str, filename: str):
    if not storage.get_claim(claim_id):
        raise HTTPException(status_code=404, detail="Claim not found")
    deleted = storage.delete_claim_image(claim_id, filename)
    if not deleted:
        raise HTTPException(status_code=404, detail="Image not found")
    return {"deleted": filename}


# ── File Upload ───────────────────────────────────────────────────────────────

@app.post("/api/claims/{claim_id}/files")
async def upload_files(
    claim_id: str,
    files: list[UploadFile] = File(...),
    doc_type: str = Form("images"),
):
    claim = storage.get_claim(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if doc_type not in ("images", "estimate", "fir"):
        raise HTTPException(status_code=400, detail=f"Invalid doc_type '{doc_type}'")
    file_dir = "images" if doc_type == "images" else f"docs/{doc_type}"
    saved = []
    for f in files:
        content = await f.read()
        fname = f.filename or "upload"
        path = storage.save_uploaded_file(claim_id, fname, content, file_dir)
        saved.append({"filename": fname, "path": path})
    return {"uploaded": saved}


# ── Document List ─────────────────────────────────────────────────────────────

@app.get("/api/claims/{claim_id}/docs")
def get_claim_docs(claim_id: str):
    if not storage.get_claim(claim_id):
        raise HTTPException(status_code=404, detail="Claim not found")
    from services.document_parser import get_parsed_doc
    raw = storage.get_claim_docs(claim_id)
    files: dict = {}
    for doc_type, paths in raw.items():
        files[doc_type] = [
            f"/uploads/{claim_id}/docs/{doc_type}/{os.path.basename(p)}"
            for p in paths
        ]
    parsed = {
        "estimate": get_parsed_doc(claim_id, "estimate"),
        "fir": get_parsed_doc(claim_id, "fir"),
    }
    return {"parsed": parsed, "files": files}


# ── Investigation ─────────────────────────────────────────────────────────────

@app.post("/api/claims/{claim_id}/investigate")
async def investigate(claim_id: str, background_tasks: BackgroundTasks):
    claim = storage.get_claim(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    background_tasks.add_task(_run_investigation, claim_id, claim)
    return {"message": "Investigation started", "claim_id": claim_id}


async def _run_investigation(claim_id: str, claim: dict):
    from orchestrator import run_pipeline
    queue = get_sse_queue(claim_id)
    await run_pipeline(claim_id, claim, queue)


# ── SSE Stream ────────────────────────────────────────────────────────────────

@app.get("/api/claims/{claim_id}/stream")
async def stream(claim_id: str):
    queue = get_sse_queue(claim_id)

    async def event_generator() -> AsyncGenerator[dict, None]:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=60)
                yield {"data": json.dumps(event)}
                if event.get("type") == "done":
                    break
            except asyncio.TimeoutError:
                yield {"data": json.dumps({"type": "ping"})}

    return EventSourceResponse(event_generator())


# ── Human-in-the-loop: Adjuster decision & notes ──────────────────────────────

# Maps an adjuster's chosen decision to the claim's lifecycle status.
_ADJUSTER_STATUS = {
    "Approve":  "Approved",
    "Reject":   "Rejected",
    "Settle":   "Settled",
    "Escalate": "Escalated",
    "Request Info": "Pending Customer",
}


class AdjusterDecisionIn(BaseModel):
    decision: str
    adjuster: str = "Adjuster"
    reason: str = ""


class AdjusterNoteIn(BaseModel):
    author: str = "Adjuster"
    text: str


@app.post("/api/claims/{claim_id}/adjuster/decision")
def adjuster_decision(claim_id: str, payload: AdjusterDecisionIn):
    if not storage.get_claim(claim_id):
        raise HTTPException(status_code=404, detail="Claim not found")
    if payload.decision not in _ADJUSTER_STATUS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid decision '{payload.decision}'. Allowed: {list(_ADJUSTER_STATUS)}",
        )

    result = storage.get_result(claim_id) or {}
    ai_decision = (result.get("summary") or {}).get("decision", "")

    record = storage.set_adjuster_decision(
        claim_id, payload.decision, payload.adjuster, payload.reason, ai_decision
    )
    storage.update_claim_field(claim_id, "status", _ADJUSTER_STATUS[payload.decision])
    return {"adjuster_decision": record, "status": _ADJUSTER_STATUS[payload.decision]}


@app.post("/api/claims/{claim_id}/adjuster/notes")
def adjuster_note(claim_id: str, payload: AdjusterNoteIn):
    if not storage.get_claim(claim_id):
        raise HTTPException(status_code=404, detail="Claim not found")
    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="Note text cannot be empty")
    note = storage.add_adjuster_note(claim_id, payload.author, payload.text.strip())
    return {"note": note}


# ── Customer Communication: auto-drafted decision letter (#7) ──────────────────

@app.get("/api/claims/{claim_id}/letter")
def decision_letter(claim_id: str):
    claim = storage.get_claim(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    result = storage.get_result(claim_id)
    if not result or not (result.get("summary") or {}).get("decision"):
        raise HTTPException(status_code=400, detail="Investigation not complete — run investigation first")
    policy = storage.get_policy_by_phone(claim.get("phone", ""))

    from services.letter_generator import draft_decision_letter
    letter = draft_decision_letter(claim, result, policy)
    return letter


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/api/analytics")
def analytics():
    """
    Aggregate all claims + results into dashboard-ready stats.
    Response shape:
    {
      decisions: [{name, value}],
      claim_types: [{name, value}],
      fraud_scores: [{claim_id, score, date}],
      top_indicators: [{indicator, count}],
      settlement_by_vehicle: [{vehicle, avg_settlement, count}],
      totals: {claims, investigated, avg_fraud_score, total_settled}
    }
    """
    claims = storage.get_all_claims()

    def _settlement_amount(summary: dict) -> int:
        """Payout figure for a claim — prefer the transparent breakdown net payable."""
        bd = summary.get("settlement_breakdown") or {}
        if bd.get("net_payable"):
            return int(bd["net_payable"])
        return int(summary.get("recommended_settlement", 0) or 0)

    def _claimed_amount(claim: dict, summary: dict, bd: dict) -> int:
        """Best estimate of what the claimant effectively asked to be paid."""
        if bd.get("amount_claimed"):
            return int(bd["amount_claimed"])
        try:
            g = float(claim.get("garage_estimate_amount") or 0)
            if g > 0:
                return int(g)
        except (ValueError, TypeError):
            pass
        if bd.get("assessed_fair_value"):
            return int(bd["assessed_fair_value"])
        rec = summary.get("recommended_settlement") or 0
        if rec:
            return int(rec)
        try:
            return int(float(claim.get("claim_amount") or 0))
        except (ValueError, TypeError):
            return 0

    decisions: dict[str, int] = {}
    claim_types: dict[str, int] = {}
    fraud_scores: list[dict] = []
    indicator_counts: dict[str, int] = {}
    settlement_by_vehicle: dict[str, list[int]] = {}
    total_settled = 0          # money a HUMAN actually approved/settled
    pending_settlement = 0     # AI-approved but awaiting human sign-off (exposure)
    awaiting_review = 0        # investigated, no human decision yet
    human_reviewed = 0         # has an adjuster decision recorded
    fraud_score_sum = 0
    investigated = 0

    # ── #1 Financial ──────────────────────────────────────────────────────────
    leakage_prevented = 0       # rejected claim amounts + disallowed inflation
    deductions_recovered = 0    # depreciation + deductible + salvage on approved
    total_claimed_approved = 0  # for settlement-ratio
    payout_amounts: list[int] = []

    # ── #2 Fraud & Risk ───────────────────────────────────────────────────────
    fraud_levels = {"Low": 0, "Medium": 0, "High": 0}
    scheme_counts: dict[str, int] = {}
    fraud_exposure_stopped = 0  # ₹ of High-risk claims not paid out

    # ── #4 AI ↔ Human Governance ──────────────────────────────────────────────
    gov_agreed = 0
    gov_overridden = 0
    override_matrix: dict[tuple, int] = {}
    confidence_sum = 0
    confidence_n = 0
    needs_review_count = 0
    image_gate_failed = 0

    for c in claims:
        # Claim type tally
        ct = c.get("claim_type", "Unknown")
        claim_types[ct] = claim_types.get(ct, 0) + 1

        result = storage.get_result(c["claim_id"])
        if not result or not result.get("summary", {}).get("decision"):
            continue

        investigated += 1
        summary = result["summary"]
        agents  = result.get("agents", {})
        adj     = result.get("adjuster_decision") or {}
        human_decision = adj.get("decision")   # None until an adjudicator acts

        # Decisions (AI recommendation)
        dec = summary.get("decision", "Unknown")
        decisions[dec] = decisions.get(dec, 0) + 1

        # Fraud score timeline
        score = summary.get("fraud_risk_score", 0)
        fraud_score_sum += score
        fraud_scores.append({
            "claim_id": c["claim_id"],
            "claimant": c.get("claimant", ""),
            "score": score,
            "label": summary.get("fraud_risk_label", ""),
            "date": c.get("created_at", ""),
        })

        # Settlement — only count money a HUMAN actually approved as "settled"
        amount  = _settlement_amount(summary)
        vehicle = c.get("vehicle", "Unknown")
        if human_decision:
            human_reviewed += 1
            if human_decision in ("Approve", "Settle"):
                total_settled += amount
                if amount > 0:
                    settlement_by_vehicle.setdefault(vehicle, []).append(amount)
        else:
            awaiting_review += 1
            if dec == "Approve":
                pending_settlement += amount

        # Fraud indicators
        indicators = agents.get("fraud_intelligence", {}).get("indicators", [])
        for ind in indicators:
            # Extract the indicator ID if present (e.g. "FI-POL-001: ...")
            key = ind.split(":")[0].strip() if ":" in ind else ind[:60]
            indicator_counts[key] = indicator_counts.get(key, 0) + 1

        # ── Breakdown (compute on the fly if a pre-feature claim lacks it) ──────
        breakdown = summary.get("settlement_breakdown") or {}
        if not breakdown:
            try:
                from services.settlement_calc import compute_settlement_breakdown
                policy_c = storage.get_policy_by_phone(c.get("phone", ""))
                breakdown = compute_settlement_breakdown(
                    c, policy_c, agents.get("damage_assessment", {}), dec
                ) or {}
            except Exception:
                breakdown = {}

        effective = human_decision or dec
        claimed = _claimed_amount(c, summary, breakdown)
        disallowed = int(breakdown.get("disallowed_inflation") or 0)

        # ── #1 Financial ───────────────────────────────────────────────────────
        if effective == "Reject":
            leakage_prevented += claimed          # full ask blocked
        leakage_prevented += disallowed           # inflation trimmed (any claim)

        if human_decision in ("Approve", "Settle"):
            deductions_recovered += (
                int(breakdown.get("depreciation") or 0)
                + int(breakdown.get("compulsory_deductible") or 0)
                + int(breakdown.get("salvage_value") or 0)
            )
            total_claimed_approved += claimed
            payout_amounts.append(_settlement_amount(summary))

        # ── #2 Fraud & Risk ──────────────────────────────────────────────────
        flabel = summary.get("fraud_risk_label", "Unknown")
        if flabel in fraud_levels:
            fraud_levels[flabel] += 1
        for s in (agents.get("fraud_intelligence", {}).get("matched_schemes") or []):
            sk = s.split(":")[0].strip() if ":" in s else s[:50]
            scheme_counts[sk] = scheme_counts.get(sk, 0) + 1
        if flabel == "High" and effective in ("Reject", "Escalate"):
            fraud_exposure_stopped += claimed

        # ── #4 AI ↔ Human Governance ─────────────────────────────────────────
        conf = summary.get("overall_confidence")
        if isinstance(conf, (int, float)):
            confidence_sum += conf
            confidence_n += 1
        if summary.get("needs_human_review"):
            needs_review_count += 1
        iq = summary.get("image_quality") or {}
        if iq and not iq.get("gate_passed", True):
            image_gate_failed += 1
        if human_decision:
            if human_decision == dec:
                gov_agreed += 1
            else:
                gov_overridden += 1
                mk = (dec or "?", human_decision)
                override_matrix[mk] = override_matrix.get(mk, 0) + 1

    # Sort and shape outputs
    top_indicators = sorted(
        [{"indicator": k, "count": v} for k, v in indicator_counts.items()],
        key=lambda x: -x["count"]
    )[:10]

    settlement_out = [
        {
            "vehicle": v,
            "avg_settlement": int(sum(amounts) / len(amounts)),
            "count": len(amounts),
        }
        for v, amounts in sorted(settlement_by_vehicle.items())
    ]

    avg_fraud = round(fraud_score_sum / investigated, 1) if investigated else 0

    # Payout size distribution (human-approved claims)
    _buckets = [
        ("< ₹10k", 0, 10000),
        ("₹10k–50k", 10000, 50000),
        ("₹50k–1L", 50000, 100000),
        ("> ₹1L", 100000, float("inf")),
    ]
    payout_buckets = [
        {"name": name, "value": sum(1 for a in payout_amounts if lo <= a < hi)}
        for name, lo, hi in _buckets
    ]

    settlement_ratio_pct = (
        round(total_settled / total_claimed_approved * 100)
        if total_claimed_approved else None
    )

    matched_schemes = sorted(
        [{"name": k, "count": v} for k, v in scheme_counts.items()],
        key=lambda x: -x["count"],
    )[:8]

    avg_confidence = round(confidence_sum / confidence_n) if confidence_n else None
    agreement_rate = (
        round(gov_agreed / human_reviewed * 100) if human_reviewed else None
    )
    override_out = sorted(
        [{"ai": ai, "human": human, "count": ct} for (ai, human), ct in override_matrix.items()],
        key=lambda x: -x["count"],
    )

    return {
        "decisions": [{"name": k, "value": v} for k, v in sorted(decisions.items())],
        "claim_types": [{"name": k, "value": v} for k, v in sorted(claim_types.items(), key=lambda x: -x[1])],
        "fraud_scores": sorted(fraud_scores, key=lambda x: x["date"]),
        "top_indicators": top_indicators,
        "settlement_by_vehicle": settlement_out,
        "totals": {
            "claims": len(claims),
            "investigated": investigated,
            "avg_fraud_score": avg_fraud,
            "total_settled": total_settled,          # human-approved payouts
            "pending_settlement": pending_settlement, # AI-approved, awaiting human
            "awaiting_review": awaiting_review,
            "human_reviewed": human_reviewed,
        },
        # ── #1 Financial ────────────────────────────────────────────────────────
        "financial": {
            "leakage_prevented": leakage_prevented,
            "deductions_recovered": deductions_recovered,
            "settlement_ratio_pct": settlement_ratio_pct,
            "total_claimed_approved": total_claimed_approved,
            "payout_buckets": payout_buckets,
        },
        # ── #2 Fraud & Risk ──────────────────────────────────────────────────────
        "fraud": {
            "levels": [{"name": k, "value": fraud_levels[k]} for k in ("Low", "Medium", "High")],
            "matched_schemes": matched_schemes,
            "exposure_stopped": fraud_exposure_stopped,
        },
        # ── #4 AI ↔ Human Governance ─────────────────────────────────────────────
        "governance": {
            "reviewed": human_reviewed,
            "agreed": gov_agreed,
            "overridden": gov_overridden,
            "agreement_rate": agreement_rate,
            "override_matrix": override_out,
            "avg_confidence": avg_confidence,
            "needs_review": needs_review_count,
            "image_gate_failed": image_gate_failed,
        },
    }


# ── PDF Report Download ───────────────────────────────────────────────────────

@app.get("/api/claims/{claim_id}/report")
def download_report(claim_id: str):
    """
    Generate and return a full explainable PDF investigation report.
    Downloads as: ClaimIntel_Report_<claim_id>.pdf
    """
    claim = storage.get_claim(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    result = storage.get_result(claim_id)
    if not result or not result.get("summary", {}).get("decision"):
        raise HTTPException(status_code=400, detail="Investigation not complete — run investigation first")

    from services.report_generator import generate_claim_report
    pdf_bytes = generate_claim_report(claim, result)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="ClaimIntel_Report_{claim_id}.pdf"',
            "Cache-Control": "no-cache",
        },
    )
