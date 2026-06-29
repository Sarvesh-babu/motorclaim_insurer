"""
Cross-claim garage (workshop) intelligence for ClaimIntel.

Aggregates the garage-vs-AI repair-estimate variance recorded by the Damage
Assessment agent (`garage_vs_ai_variance_pct`, `garage_inflation_flag`) across
every investigated claim, keyed by the garage/workshop the claimant nominated
(`garage_workshop_name` on the claim).

Two consumers:
  • Fraud Intelligence FC-24 — `cross_claim_inflation(claim)` flags a claim whose
    nominated garage shows a systematic inflation pattern across PRIOR claims
    (FS-004: Workshop Inflation Conspiracy).
  • Analytics — `garage_stats(records)` powers the Garage Risk Intelligence table.

All numbers are deterministic (no LLM) and computed straight from result.json.
"""

from statistics import mean

import storage

# A single claim's garage estimate is "inflated" when it sits this far above the
# AI assessed fair value — mirrors damage_assessment.garage_inflation_flag and
# settlement_calc.REVIEW_PCT so the whole system agrees on the 40% line.
INFLATION_PCT = 40


def _norm(name: str) -> str:
    """Canonical key for a garage name — case/space-insensitive."""
    return " ".join((name or "").strip().lower().split())


def collect_garage_records(exclude_claim_id: str | None = None) -> list[dict]:
    """Scan all claims + result.json, return one record per claim that named a
    garage AND has a recorded garage-vs-AI variance.

    Record: {claim_id, claimant, garage, garage_key, variance_pct,
             garage_amount, ai_value, inflated}
    """
    records: list[dict] = []
    for c in storage.get_all_claims():
        cid = c.get("claim_id")
        if exclude_claim_id and cid == exclude_claim_id:
            continue
        garage = (c.get("garage_workshop_name") or "").strip()
        if not garage:
            continue
        res = storage.get_result(cid)
        if not res:
            continue
        dmg = (res.get("agents") or {}).get("damage_assessment") or {}
        if not dmg.get("garage_estimate_provided"):
            continue
        variance = dmg.get("garage_vs_ai_variance_pct")
        if variance is None:
            continue
        try:
            variance = float(variance)
        except (ValueError, TypeError):
            continue
        records.append({
            "claim_id":      cid,
            "claimant":      c.get("claimant", ""),
            "garage":        garage,
            "garage_key":    _norm(garage),
            "variance_pct":  round(variance, 1),
            "garage_amount": dmg.get("garage_estimate_amount_inr"),
            "inflated":      bool(dmg.get("garage_inflation_flag")),
        })
    return records


def _risk_level(count: int, avg_variance: float, inflation_rate: float) -> str:
    """High / Medium / Low / Clear from a garage's aggregate behaviour."""
    if avg_variance > INFLATION_PCT or (inflation_rate >= 0.5 and count >= 2):
        return "High"
    if avg_variance > 20 or inflation_rate > 0:
        return "Medium"
    if avg_variance > 0:
        return "Low"
    return "Clear"


def garage_stats(records: list[dict]) -> list[dict]:
    """Aggregate records by garage, ranked worst-first (highest avg variance).

    Each row: {garage, claims, avg_variance_pct, inflation_count,
               inflation_rate_pct, risk_level}
    """
    by_key: dict[str, dict] = {}
    for r in records:
        g = by_key.setdefault(r["garage_key"], {"garage": r["garage"], "variances": [], "inflated": 0})
        g["variances"].append(r["variance_pct"])
        if r["inflated"]:
            g["inflated"] += 1

    rows: list[dict] = []
    for g in by_key.values():
        count = len(g["variances"])
        avg_var = round(mean(g["variances"]), 1) if count else 0.0
        inflation_rate = g["inflated"] / count if count else 0.0
        rows.append({
            "garage":             g["garage"],
            "claims":             count,
            "avg_variance_pct":   avg_var,
            "inflation_count":    g["inflated"],
            "inflation_rate_pct": round(inflation_rate * 100),
            "risk_level":         _risk_level(count, avg_var, inflation_rate),
        })

    rows.sort(key=lambda x: (-x["avg_variance_pct"], -x["claims"]))
    return rows


def cross_claim_inflation(claim: dict) -> dict:
    """FC-24 — does this claim's nominated garage show a systematic inflation
    pattern across PRIOR investigated claims?

    Returns a check dict: {status: pass|flag|na, detail: str}.
    """
    garage = (claim.get("garage_workshop_name") or "").strip()
    if not garage:
        return {"status": "na", "detail": "No garage/workshop named on this claim."}

    key = _norm(garage)
    priors = [r for r in collect_garage_records(exclude_claim_id=claim.get("claim_id"))
              if r["garage_key"] == key]

    if not priors:
        return {"status": "pass",
                "detail": f"No prior investigated claims at '{garage}' — no cross-claim pattern."}

    stats = garage_stats(priors)[0]   # single garage → one row
    risk = stats["risk_level"]

    if risk in ("High", "Medium"):
        return {
            "status": "flag",
            "detail": (
                f"FS-004: Cross-claim workshop inflation — '{garage}' has "
                f"{stats['claims']} prior claim(s), avg variance "
                f"{stats['avg_variance_pct']:+.0f}% vs AI estimate "
                f"({stats['inflation_count']} flagged as inflated). "
                f"Systematic workshop inflation pattern ({risk} risk)."
            ),
        }

    return {
        "status": "pass",
        "detail": (
            f"'{garage}' has {stats['claims']} prior claim(s), avg variance "
            f"{stats['avg_variance_pct']:+.0f}% — no systematic inflation."
        ),
    }
