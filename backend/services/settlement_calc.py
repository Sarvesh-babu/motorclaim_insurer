"""
Transparent settlement math for ClaimIntel.

Produces an itemised breakdown so the headline settlement figure always
reconciles with visible line items (the "real claim sheet" feel):

    Repair estimate
  - Depreciation (on parts, per IRDAI age schedule)
  - Compulsory deductible
  - Salvage value
  + GST on labour
  = Net payable

This is fully deterministic (no LLM) so the numbers are always reproducible.
"""

from datetime import datetime
from typing import Optional


# IRDAI parts-depreciation by vehicle age — mirrors
# data/kb/fraud_indicators.json -> irdai_guidelines.depreciation_schedule
def _depreciation_pct(age_years: float) -> int:
    if age_years <= 0.5:
        return 5
    if age_years <= 1:
        return 15
    if age_years <= 2:
        return 20
    if age_years <= 3:
        return 30
    if age_years <= 4:
        return 40
    return 50  # 4+ years caps at 50% under the standard schedule


# IRDAI compulsory deductible by engine displacement (private car)
COMP_DED_SMALL = 1000   # ≤ 1500 cc
COMP_DED_LARGE = 2000   # > 1500 cc  (Innova, Scorpio, BMW etc.)

# Total-loss threshold — if repair ≥ this % of sum insured, it's a total loss
TOTAL_LOSS_PCT = 75

PARTS_FRACTION   = 0.70   # ~70% of a repair invoice is parts
LABOUR_FRACTION  = 0.30   # ~30% labour
GST_RATE         = 0.18   # 18% GST — already baked into the repair invoice total

# IRDAI material-specific depreciation — overrides the blanket age-slab for
# parts whose depreciation is fixed regardless of vehicle age.
# Key = canonical part name fragment;  value = fixed depreciation %
MATERIAL_DEP_OVERRIDES: dict[str, int] = {
    # Glass — 0% depreciation (IRDAI: no depreciation on glass parts)
    "windshield": 0, "glass": 0, "rear_glass": 0,
    # Tyres, tubes, batteries — 50% regardless of age
    "tyre": 50, "tire": 50, "battery": 50, "tube": 50,
    # Rubber/plastic — 50%
    "rubber": 50, "plastic": 50,
    # Fibre-reinforced parts — 30%
    "fibreglass": 30, "fibre": 30,
}


def _part_dep_pct(part_name: str, age_dep_pct: int) -> int:
    """Return the correct depreciation % for a part — material overrides beat age slab."""
    key = (part_name or "").lower()
    for fragment, fixed_pct in MATERIAL_DEP_OVERRIDES.items():
        if fragment in key:
            return fixed_pct
    return age_dep_pct


def _compulsory_deductible(policy: dict) -> int:
    """₹1,000 for ≤1,500cc; ₹2,000 for >1,500cc (IRDAI motor private-car schedule)."""
    try:
        cc = int(str(policy.get("engine_cc") or "0").strip())
        if cc == 0:          # EV or unknown → use small-car rate
            return COMP_DED_SMALL
        return COMP_DED_LARGE if cc > 1500 else COMP_DED_SMALL
    except (ValueError, TypeError):
        return COMP_DED_SMALL


def _voluntary_deductible(policy: dict) -> int:
    try:
        return int(float(str(policy.get("voluntary_deductible") or "0").strip()))
    except (ValueError, TypeError):
        return 0



def _ncb_pct(policy: dict) -> int:
    try:
        return int(float(str(policy.get("ncb_pct") or "0").strip()))
    except (ValueError, TypeError):
        return 0


def _sum_insured(policy: dict) -> int:
    try:
        return int(float(str(policy.get("sum_insured") or "0").strip()))
    except (ValueError, TypeError):
        return 0


def _vehicle_age_years(policy: dict, claim: dict) -> Optional[float]:
    try:
        year = int(str(policy.get("vehicle_year", "")).strip())
        inc = datetime.strptime(claim.get("incident_date", "")[:10], "%Y-%m-%d").date()
        age = (inc.year + (inc.month - 1) / 12) - year
        return max(0.0, age)
    except (ValueError, TypeError):
        return None


# ── Over-claim bands ────────────────────────────────────────────────────────────
# How far the claimed/garage amount may sit ABOVE the independent assessed value
# ── Fair-value risk bands (variance between claimed and assessed) ──────────────
# These thresholds classify the garage quote relative to the AI fair value.
# They are FRAUD DETECTION SIGNALS ONLY and never affect the payout.
CONSISTENT_RANGE = 20   # within ±20% → Auto Process
REVIEW_PCT       = 40   # +20% to +40% → Review Required
                         # > +40%       → Investigation Required

# Recommended actions per band — displayed to adjudicator, no effect on math
BAND_ACTIONS = {
    "consistent":    "Auto Process",
    "review":        "Review Required",
    "investigate":   "Investigation Required",
}

_SOURCE_LABELS = {
    "live_web":        "Live web market prices",
    "vehicle_catalog": "OEM parts catalog (exact model)",
    "segment_catalog": "Segment parts catalog",
}


def _assess_repair(damage: dict) -> dict:
    """Fair-value analysis + anti-inflation cap.

    Compares the garage quoted amount against the AI's independently assessed
    fair value and produces a risk band + recommended action for the adjudicator.

    PAYOUT IMPACT: the garage quote is used as the approved repair basis for the
    "consistent" (±20%) and "review" (+20–40%) bands. For the "investigate" band
    (garage quote >40% above the assessed fair value) the repair basis is CAPPED
    at the AI assessed fair value, and the excess is surfaced as
    `disallowed_inflation` (leakage prevented). `inflation_cap_applied` records
    whether the cap fired.
    """
    est = damage.get("total_repair_estimate") or {}
    lo  = float(est.get("min") or 0) if isinstance(est, dict) else 0.0
    hi  = float(est.get("max") or 0) if isinstance(est, dict) else 0.0
    assessed_mid = (lo + hi) / 2 if hi > 0 else float(lo)
    assessed_max = hi if hi > 0 else assessed_mid

    claimed = 0.0
    if damage.get("garage_estimate_provided"):
        try:
            claimed = float(damage.get("garage_estimate_amount_inr") or 0)
        except (ValueError, TypeError):
            claimed = 0.0

    source       = damage.get("pricing_method") or "segment_catalog"
    source_label = _SOURCE_LABELS.get(source, "Parts catalog")

    out = {
        "assessed_fair_value": round(assessed_mid) if assessed_mid > 0 else None,
        "assessed_band_min":   round(lo)           if lo > 0          else None,
        "assessed_band_max":   round(assessed_max) if assessed_max > 0 else None,
        "benchmark_source":    source_label,
        "amount_claimed":      round(claimed)      if claimed > 0     else None,
        "overclaim_pct":       None,
        "overclaim_band":      None,
        "overclaim_note":      None,
        "recommended_action":  None,
        "disallowed_inflation": 0,
        "inflation_cap_applied": False,
    }

    # No independent assessment — use whatever figure is available as repair basis
    if assessed_mid <= 0:
        out["approved_basis"] = round(claimed)
        return out

    # No garage quote — use the assessed fair value as repair basis
    if claimed <= 0:
        out["approved_basis"]  = round(assessed_mid)
        out["overclaim_band"]  = "consistent"
        out["recommended_action"] = BAND_ACTIONS["consistent"]
        out["overclaim_note"]  = (
            f"No garage quote provided — repair basis is the independently "
            f"assessed fair value of ₹{assessed_mid:,.0f} ({source_label})."
        )
        return out

    variance_pct = (claimed - assessed_mid) / assessed_mid * 100

    # ── Risk band classification ───────────────────────────────────────────────
    if variance_pct > REVIEW_PCT:
        band = "investigate"
    elif variance_pct > CONSISTENT_RANGE:
        band = "review"
    else:
        band = "consistent"   # covers ±20% (both above and below)

    out["overclaim_pct"]        = round(variance_pct, 1)
    out["overclaim_band"]       = band
    out["recommended_action"]   = BAND_ACTIONS[band]
    out["underclaim_advisory"]  = None

    if band == "consistent":
        # Garage quote within ±20% — accepted in full as the repair basis.
        out["approved_basis"]       = round(claimed)
        out["disallowed_inflation"] = 0
        out["inflation_cap_applied"] = False
        out["overclaim_note"] = (
            f"Claimed ₹{claimed:,.0f} is within ±20% of the assessed fair value "
            f"of ₹{assessed_mid:,.0f} ({source_label}) — repair cost is consistent."
        )
    elif band == "review":
        # +20%–40% — elevated but not capped; garage quote still the repair basis,
        # surfaced to the adjudicator for line-item review.
        out["approved_basis"]       = round(claimed)
        out["disallowed_inflation"] = 0
        out["inflation_cap_applied"] = False
        out["overclaim_note"] = (
            f"Claimed ₹{claimed:,.0f} is {variance_pct:.1f}% above the assessed "
            f"fair value of ₹{assessed_mid:,.0f} — review required."
        )
    else:  # investigate — garage quote >40% above fair value → apply inflation cap
        disallowed = max(round(claimed - assessed_mid), 0)
        out["approved_basis"]        = round(assessed_mid)   # payout on AI fair value, not the inflated quote
        out["disallowed_inflation"]  = disallowed
        out["inflation_cap_applied"] = True
        out["overclaim_note"] = (
            f"Claimed ₹{claimed:,.0f} is {variance_pct:.1f}% above the assessed fair "
            f"value of ₹{assessed_mid:,.0f} — exceeds the {REVIEW_PCT}% investigation "
            f"threshold. Payout capped at the AI assessed fair value; "
            f"₹{disallowed:,.0f} disallowed as inflation (leakage prevented)."
        )

    return out


def compute_settlement_breakdown(
    claim: dict,
    policy: Optional[dict],
    damage: dict,
    decision: str,
) -> Optional[dict]:
    """Return an itemised, IRDAI-aligned settlement breakdown.

    Handles: total-loss/IDV path, material-wise depreciation, zero-dep add-on,
    engine-cc compulsory deductible, voluntary deductible, parts + labour GST,
    IDV/sum-insured payout cap, and NCB impact advisory.
    """
    policy = policy or {}
    assess = _assess_repair(damage or {})
    repair = round(assess.get("approved_basis") or 0)
    si     = _sum_insured(policy)

    # ── Total-loss check ────────────────────────────────────────────────────────
    # If repair ≥ 75% of sum insured, the vehicle is a constructive total loss.
    # Settlement = IDV (sum insured) − salvage, not repair cost.
    is_total_loss = False
    total_loss_note = None
    if repair <= 0 and si <= 0:
        return None

    if si > 0 and repair > 0 and repair >= (TOTAL_LOSS_PCT / 100) * si:
        is_total_loss = True
        salvage       = round(si * 0.05)   # typical 5% salvage assumption
        total_loss_note = (
            f"Repair estimate ₹{repair:,} is ≥ {TOTAL_LOSS_PCT}% of sum insured "
            f"₹{si:,} — treated as constructive total loss. "
            f"Settlement = IDV − salvage (₹{si:,} − ₹{salvage:,})."
        )
        repair = si   # settle on IDV

    # ── Policy deductibles ────────────────────────────────────────────────────────
    comp_ded  = _compulsory_deductible(policy)
    vol_ded   = _voluntary_deductible(policy)
    total_ded = comp_ded + vol_ded

    # ── Depreciation (material-specific, per IRDAI) ─────────────────────────────
    age     = _vehicle_age_years(policy, claim or {})
    age_dep = _depreciation_pct(age) if age is not None else 0

    depreciation = 0
    dep_note      = ""
    if is_total_loss:
        dep_note = "Total loss — depreciation not applicable (IDV already accounts for age)."
    else:
        damaged_parts = (damage or {}).get("damaged_parts") or []
        if damaged_parts:
            # Per-part material depreciation
            for p in damaged_parts:
                est   = p.get("repair_estimate_INR") or {}
                p_min = float(est.get("min") or 0)
                p_max = float(est.get("max") or 0)
                p_mid = (p_min + p_max) / 2 if p_max > 0 else p_min
                p_parts_val = p_mid * PARTS_FRACTION
                p_dep_pct   = _part_dep_pct(p.get("part", ""), age_dep)
                depreciation += p_parts_val * p_dep_pct / 100
            depreciation = round(depreciation)
        else:
            # Fallback: blanket 70% parts assumption
            depreciation = round(repair * PARTS_FRACTION * age_dep / 100)
        dep_note = (
            f"Material-specific depreciation (IRDAI): glass 0%, rubber/tyre 50%, "
            f"metal {age_dep}% — vehicle age ~{round(age, 1) if age else '?'} yr."
        )

    # ── GST (already embedded — informational only) ─────────────────────────────
    # Indian repair invoices quote a GST-INCLUSIVE grand total: the garage's
    # ₹X already contains 18% GST. Adding GST again on top would double-count it
    # and wipe out the depreciation/deductible reductions. So we DO NOT add GST
    # to the payout — we only surface the GST already inside the repair total
    # (total − total/1.18) for transparency.
    if is_total_loss:
        gst_included = 0
    else:
        gst_included = round(repair - repair / (1 + GST_RATE))

    salvage = round(si * 0.05) if is_total_loss else 0

    # ── Net payable ─────────────────────────────────────────────────────────────
    # Repair total is GST-inclusive, so deductions simply reduce it.
    net = round(repair - depreciation - total_ded - salvage)
    net = max(net, 0)

    # Cap at sum insured / IDV — you can never receive more than the IDV
    if si > 0:
        net = min(net, si)

    # ── NCB advisory ────────────────────────────────────────────────────────────
    ncb = _ncb_pct(policy)
    ncb_advisory = None
    if ncb > 0 and decision == "Approve":
        annual_premium = float(policy.get("annual_premium") or 0)
        ncb_saving     = round(annual_premium * ncb / 100)
        ncb_advisory   = (
            f"Approving this claim will reset your {ncb}% No-Claim Bonus. "
            f"That NCB was saving you approximately ₹{ncb_saving:,} per year on your premium. "
            f"Consider whether the claim amount justifies losing this discount."
        )

    assumptions = (
        f"Depreciation: {dep_note} | "
        f"Deductibles: compulsory ₹{comp_ded:,} + voluntary ₹{vol_ded:,} | "
        f"GST: 18% included in repair total | "
        f"Payout capped at sum insured ₹{si:,}."
    ) if not is_total_loss else (
        f"Total loss settlement: IDV ₹{si:,} − salvage ₹{salvage:,} | "
        f"Deductibles: ₹{total_ded:,} | Payout capped at IDV."
    )

    # Inflation cap only carries meaning on the repair (non-total-loss) path —
    # a total loss already settles on IDV, not the garage quote.
    cap_applied = bool(assess.get("inflation_cap_applied")) and not is_total_loss
    settlement_basis = "AI Assessed Fair Value (Inflation Cap)" if cap_applied else "Human Approved Amount"

    return {
        "is_total_loss":         is_total_loss,
        "total_loss_note":       total_loss_note,
        "repair_estimate":       repair,
        # Fair-value audit fields (from _assess_repair)
        "assessed_fair_value":   assess.get("assessed_fair_value"),
        "assessed_band_min":     assess.get("assessed_band_min"),
        "assessed_band_max":     assess.get("assessed_band_max"),
        "benchmark_source":      assess.get("benchmark_source"),
        "amount_claimed":        assess.get("amount_claimed"),
        "overclaim_pct":         assess.get("overclaim_pct"),
        "overclaim_band":        assess.get("overclaim_band"),
        "overclaim_note":        assess.get("overclaim_note"),
        "recommended_action":    assess.get("recommended_action"),
        "disallowed_inflation":  assess.get("disallowed_inflation") or 0,
        "inflation_cap_applied": cap_applied,
        "underclaim_advisory":   assess.get("underclaim_advisory"),
        "settlement_basis":      settlement_basis,
        # Depreciation
        "vehicle_age_years":     round(age, 1) if age is not None else None,
        "depreciation_pct":      0 if is_total_loss else age_dep,
        "depreciation":          depreciation,
        "depreciation_note":     dep_note,
        # Deductibles
        "compulsory_deductible": comp_ded,
        "voluntary_deductible":  vol_ded,
        "total_deductible":      total_ded,
        # GST (already included in the GST-inclusive repair total — informational)
        "gst_included":          gst_included,
        "gst_rate_pct":          int(GST_RATE * 100),
        # Final
        "salvage_value":         salvage,
        "net_payable":           net,
        "sum_insured_cap":       si,
        "applies":               decision == "Approve",
        # NCB
        "ncb_pct":               ncb,
        "ncb_advisory":          ncb_advisory,
        "assumptions":           assumptions,
    }
