"""Historical fraud-pattern cross-checks for ClaimIntel.

Distinct from garage_intel.py (which scans the LIVE operational claims.csv +
result.json — claims this deployment has actually investigated): this module
reads a standing synthetic archive (data/kb/historical_fraud_cases.json) of
previously confirmed/suspected-fraud claims, so cross-claim pattern checks
still have history to compare against even when the live queue is fresh
(claims.csv ships/resets empty, per scripts/reset_claims.py).

Three pattern checks, mirroring the request: has this exact garage, this
exact claimant, or this exact telematics-fraud signature shown up in a past
fraud case?
"""

import json
import os
import threading

from config import KB_DIR

_ARCHIVE_PATH = os.path.join(KB_DIR, "historical_fraud_cases.json")

_lock = threading.Lock()
_cache: list[dict] | None = None


def _norm(s: str | None) -> str:
    return " ".join((s or "").strip().lower().split())


def _load_cases() -> list[dict]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            with open(_ARCHIVE_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f).get("cases", [])
        except Exception:
            _cache = []
        return _cache


def garage_archive_check(claim: dict) -> dict:
    """Has this claim's nominated garage appeared in a PAST confirmed/suspected
    fraud case in the historical archive (independent of the live queue)?"""
    garage = (claim.get("garage_workshop_name") or "").strip()
    if not garage:
        return {"status": "na", "detail": "No garage/workshop named on this claim.", "matches": []}

    key = _norm(garage)
    hits = [c for c in _load_cases() if _norm(c.get("garage_workshop_name")) == key]
    if not hits:
        return {"status": "pass",
                "detail": f"'{garage}' has no record in the historical fraud archive.",
                "matches": []}

    high_risk = [c for c in hits if c.get("fraud_label") == "High"]
    rejected = [c for c in hits if c.get("decision") == "Rejected"]
    detail = (
        f"FS-004: '{garage}' appears in {len(hits)} historical fraud case(s) "
        f"({len(rejected)} rejected, {len(high_risk)} High-risk) — e.g. {hits[0]['case_id']} "
        f"({hits[0].get('fraud_label')} risk, {hits[0].get('decision')})."
    )
    return {"status": "flag", "detail": detail, "matches": [c["case_id"] for c in hits]}


def claimant_archive_check(claim: dict) -> dict:
    """Has this claimant (matched by phone, across any policy) appeared in a
    PAST confirmed/suspected fraud case in the historical archive?"""
    phone = (claim.get("phone") or "").strip().replace(" ", "").replace("-", "")
    if not phone:
        return {"status": "na", "detail": "No phone number on this claim.", "matches": []}

    hits = [c for c in _load_cases()
            if (c.get("phone") or "").strip().replace(" ", "").replace("-", "") == phone]
    if not hits:
        return {"status": "pass",
                "detail": "Claimant phone number has no record in the historical fraud archive.",
                "matches": []}

    detail = (
        f"FI-CLM-001: Claimant (phone ending {phone[-4:]}) has {len(hits)} prior fraud case(s) "
        f"on record — e.g. {hits[0]['case_id']} ({hits[0].get('fraud_label')} risk, "
        f"{hits[0].get('decision')}, {hits[0].get('vehicle')})."
    )
    return {"status": "flag", "detail": detail, "matches": [c["case_id"] for c in hits]}


def _signature_match(a: dict, b: dict) -> bool:
    """Two telematics signatures 'match' if their boolean anomaly fields agree
    (GPS mismatch, impact inconsistency, no hard braking) — the speed value
    itself can vary, only the anomaly PATTERN needs to repeat to indicate a
    reused fraud playbook."""
    keys = ("gps_location_match", "impact_consistent", "hard_braking_detected")
    return all(a.get(k) is not None and a.get(k) == b.get(k) for k in keys)


def telematics_signature_check(claim: dict, telematics: dict | None,
                                gps_check: dict | None) -> dict:
    """Does this claim's telematics anomaly pattern match a PAST fraud case's
    signature, even from a different claimant/garage — i.e. a reused fraud
    playbook rather than an isolated incident?"""
    if not telematics or not telematics.get("parsed_ok"):
        return {"status": "na", "detail": "No telematics data available.", "matches": []}

    impact_g = telematics.get("impact_g_force")
    hard_braking = telematics.get("hard_braking_detected")
    impact_consistent = (
        None if impact_g is None or hard_braking is None
        else not (hard_braking is False and impact_g < 1.0)
    )
    current_sig = {
        "gps_location_match": (gps_check or {}).get("gps_location_match"),
        "impact_consistent": impact_consistent,
        "hard_braking_detected": hard_braking,
    }
    if all(v is None for v in current_sig.values()):
        return {"status": "na", "detail": "Telematics signature could not be derived.", "matches": []}

    hits = [
        c for c in _load_cases()
        if c.get("telematics_signature") and _signature_match(current_sig, c["telematics_signature"])
    ]
    if not hits:
        return {"status": "pass",
                "detail": "Telematics anomaly pattern does not match any known fraud signature.",
                "matches": []}

    detail = (
        f"FS-002: This claim's telematics anomaly pattern "
        f"(GPS match={current_sig['gps_location_match']}, impact consistent={current_sig['impact_consistent']}, "
        f"hard braking={current_sig['hard_braking_detected']}) matches {len(hits)} prior fraud case(s) — "
        f"e.g. {hits[0]['case_id']} ({hits[0].get('claimant_name')}, {hits[0].get('garage_workshop_name')}) — "
        f"suggests a reused fraud playbook, not an isolated incident."
    )
    return {"status": "flag", "detail": detail, "matches": [c["case_id"] for c in hits]}


def build_historical_fraud_layer(claim: dict, telematics: dict | None, gps_check: dict | None) -> dict:
    """Assemble the full historical cross-claim layer for the Fraud
    Intelligence agent's output."""
    return {
        "garage_history":     garage_archive_check(claim),
        "claimant_history":   claimant_archive_check(claim),
        "telematics_history": telematics_signature_check(claim, telematics, gps_check),
    }
