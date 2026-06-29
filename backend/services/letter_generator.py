"""
Auto-drafted customer decision letter (#7 — customer communication).

Generates a ready-to-send approval / rejection / escalation letter from the
investigation result, using the same LLM client as the agents. The adjuster's
human decision (if recorded) takes precedence over the AI recommendation.
"""

from typing import Optional

from services.llm_client import ask_text


def _effective_decision(result: dict) -> tuple[str, str]:
    """Returns (decision, source) — adjuster decision overrides AI when present."""
    adj = result.get("adjuster_decision") or {}
    if adj.get("decision"):
        return adj["decision"], "adjuster"
    return (result.get("summary") or {}).get("decision", "Escalate"), "ai"


def _inr(val) -> str:
    try:
        return f"Rs. {int(float(val or 0)):,}"
    except (ValueError, TypeError):
        return "Rs. 0"


PROMPT = """You are a claims officer at an Indian motor insurer drafting a formal letter to a policyholder
communicating the outcome of their motor insurance claim. Write a clear, polite, professional letter.

TONE RULES:
- Approve         -> warm, congratulatory but professional; state the payable amount and next steps.
- Reject          -> empathetic but firm; clearly state the reason; mention the right to appeal with evidence.
- Escalate/Request Info -> reassuring; explain that a senior surveyor will review and what the customer should expect.

DECISION: {decision}
SETTLEMENT AMOUNT: {settlement}

POLICYHOLDER: {claimant}
POLICY NUMBER: {policy_no}
CLAIM NUMBER: {claim_id}
VEHICLE: {vehicle}
INCIDENT DATE: {incident_date}
INCIDENT LOCATION: {location}

KEY FINDINGS (use 1-3 of these as the human-readable justification, in plain language —
do NOT mention fraud scores, model names, or internal codes):
{reasons}

SETTLEMENT BREAKDOWN (include a short itemised summary ONLY if the decision is Approve):
{breakdown}

INSTRUCTIONS:
- Address the policyholder as "Dear Mr./Ms. [Last Name]," — use title + last name only.
  NEVER combine a title with the full name (do NOT write "Dear Mr. Rohit Verma" or "Dear Mr. Priya Sharma").
- Reference the claim number and vehicle.
- Keep it to 180-260 words.
- Use Indian Rupee formatting (Rs.).
- Do NOT invent facts not supported by the findings.
- Sign off as "Claims Department, ClaimIntel Insurance".
- Return ONLY the letter body text. No subject line metadata block, no markdown, no commentary.
"""


def draft_decision_letter(claim: dict, result: dict, policy: Optional[dict]) -> dict:
    decision, source = _effective_decision(result)
    summary = result.get("summary") or {}
    settlement_agent = (result.get("agents") or {}).get("settlement_recommendation", {})

    # Reasons: prefer the LLM's plain-English decision_reasons, fall back to summary fields
    reasons = settlement_agent.get("decision_reasons") or []
    if not reasons:
        reasons = [
            f"Damage consistency with the reported incident: {summary.get('damage_consistency', 'N/A')}",
            f"Incident reconstruction consistency: {summary.get('incident_consistency', 'N/A')}",
        ]
    reasons_str = "\n".join(f"- {r}" for r in reasons[:5])

    # Breakdown block
    bd = summary.get("settlement_breakdown") or settlement_agent.get("settlement_breakdown") or {}
    if bd and decision == "Approve":
        breakdown_str = (
            f"Repair estimate {_inr(bd.get('repair_estimate'))} (GST included); "
            f"less depreciation {_inr(bd.get('depreciation'))} ({bd.get('depreciation_pct', 0)}%); "
            f"less compulsory deductible {_inr(bd.get('compulsory_deductible'))}; "
            f"net payable {_inr(bd.get('net_payable'))}."
        )
    else:
        breakdown_str = "Not applicable for this decision."

    settlement_amt = (
        _inr(bd.get("net_payable")) if (bd and decision == "Approve")
        else _inr(summary.get("recommended_settlement", 0))
    )

    prompt = PROMPT.format(
        decision=decision,
        settlement=settlement_amt,
        claimant=claim.get("claimant", "Policyholder"),
        policy_no=claim.get("policy_no", ""),
        claim_id=claim.get("claim_id", ""),
        vehicle=claim.get("vehicle", ""),
        incident_date=claim.get("incident_date", ""),
        location=claim.get("incident_location", ""),
        reasons=reasons_str,
        breakdown=breakdown_str,
    )

    try:
        letter = ask_text(prompt).strip()
    except Exception as e:
        letter = (
            f"Dear {claim.get('claimant', 'Policyholder')},\n\n"
            f"This is regarding your motor insurance claim {claim.get('claim_id', '')} "
            f"for your {claim.get('vehicle', 'vehicle')}.\n\n"
            f"Decision: {decision}. Settlement: {settlement_amt}.\n\n"
            f"(Automated draft unavailable: {e})\n\n"
            f"Regards,\nClaims Department, ClaimIntel Insurance"
        )

    return {
        "letter": letter,
        "decision": decision,
        "decision_source": source,
        "settlement_amount": settlement_amt,
    }
