"""PII masking — reduces what's sent to the third-party Groq LLM and what
ends up in logs. Fields kept unmasked (vehicle/reg no, policy_no, location,
description) are operationally required for the agents' reasoning (catalog
lookups, geocoding, fraud/damage narrative matching).
"""


def mask_phone(phone: str) -> str:
    phone = (phone or "").strip()
    if len(phone) <= 4:
        return "X" * len(phone)
    return "X" * (len(phone) - 4) + phone[-4:]


def mask_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return name
    parts = name.split()
    first = parts[0]
    initial = f" {parts[1][0]}." if len(parts) > 1 else ""
    return f"{first}{initial}"


def build_llm_safe_claim(claim: dict) -> dict:
    """Return a copy of `claim` with phone/claimant masked before it is
    embedded in any LLM prompt or logged."""
    safe = dict(claim)
    if "phone" in safe:
        safe["phone"] = mask_phone(safe["phone"])
    if "claimant" in safe:
        safe["claimant"] = mask_name(safe["claimant"])
    return safe
