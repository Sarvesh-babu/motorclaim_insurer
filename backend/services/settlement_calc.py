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
GST_LABOUR_RATE  = 0.18   # 18% GST on labour
GST_PARTS_RATE   = 0.18   # 18% GST on parts
GST_GLASS_RATE   = 0.18   # 18% GST on glass

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


def _is_zero_dep(policy: dict) -> bool:
    return str(policy.get("zero_dep") or "").strip().lower() in ("yes", "true", "1")


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
# before it stops looking normal. Mirrors real surveyor practice: a quote a little
# over the assessed cost is routine; a large gap is the workshop-inflation pattern.
ALIGNED_TOLERANCE_PCT  = 10   # within +10% of assessed → perfectly normal
INFLATION_PCT          = 40   # beyond +40% → inflation; cap payout at assessed ceiling
UNDERCLAIM_PCT         = 20   # more than 20% below assessed → flag as under-claim

_SOURCE_LABELS = {
    "live_web":        "Live web market prices",
    "vehicle_catalog": "OEM parts catalog (exact model)",
    "segment_catalog": "Segment parts catalog",
}


def _assess_repair(damage: dict) -> dict:
    """Decide the repair amount the settlement is built on, treating the AI/engine
    estimate as the *independent assessed value* (the surveyor) and the garage /
    policyholder figure as the *claimed amount* being audited against it.

    Returns the approved repair basis plus the benchmark-vs-claim audit fields so
    the UI can show "this claim is reasonable / may or may not be fraud / inflated".
    """
    est = damage.get("total_repair_estimate") or {}
    lo = float(est.get("min") or 0) if isinstance(est, dict) else 0.0
    hi = float(est.get("max") or 0) if isinstance(est, dict) else 0.0
    assessed_mid = (lo + hi) / 2 if hi > 0 else float(lo)
    assessed_max = hi if hi > 0 else assessed_mid

    claimed = 0.0
    if damage.get("garage_estimate_provided"):
        try:
            claimed = float(damage.get("garage_estimate_amount_inr") or 0)
        except (ValueError, TypeError):
            claimed = 0.0

    source = damage.get("pricing_method") or "segment_catalog"
    source_label = _SOURCE_LABELS.get(source, "Parts catalog")

    out = {
        "assessed_fair_value": round(assessed_mid) if assessed_mid > 0 else None,
        "assessed_band_min":   round(lo) if lo > 0 else None,
        "assessed_band_max":   round(assessed_max) if assessed_max > 0 else None,
        "benchmark_source":    source_label,
        "amount_claimed":      round(claimed) if claimed > 0 else None,
        "overclaim_pct":       None,
        "overclaim_band":      None,
        "overclaim_note":      None,
        "disallowed_inflation": 0,
    }

    # No independent assessment available → fall back to whatever figure we have.
    if assessed_mid <= 0:
        out["approved_basis"] = round(claimed)
        return out

    # No claimed/garage figure → settle on the assessed fair value itself.
    if claimed <= 0:
        out["approved_basis"] = round(assessed_mid)
        out["overclaim_note"] = (
            f"No garage quote provided — settled on the independently assessed "
            f"fair value of ₹{assessed_mid:,.0f} ({source_label})."
        )
        return out

    overclaim_pct = (claimed - assessed_mid) / assessed_mid * 100

    if overclaim_pct <= ALIGNED_TOLERANCE_PCT:
        band = "aligned"
    elif overclaim_pct <= INFLATION_PCT:
        band = "elevated"
    else:
        band = "inflated"

    # Approved basis: honour the actual claimed amount unless it is inflated,
    # in which case cap at the OEM-priced ceiling and disallow the excess.
    if band == "inflated":
        basis = min(claimed, assessed_max)
    else:
        basis = claimed
    disallowed = max(0.0, claimed - basis)

    if band == "aligned":
        note = (
            f"Claimed ₹{claimed:,.0f} is consistent with the independently "
            f"assessed fair value of ₹{assessed_mid:,.0f} ({source_label})."
        )
    elif band == "elevated":
        note = (
            f"Claimed ₹{claimed:,.0f} is {overclaim_pct:.0f}% above the assessed "
            f"fair value of ₹{assessed_mid:,.0f} — above the normal range but "
            f"within tolerance. May or may not be fraud; surveyor line-item review "
            f"recommended."
        )
    else:  # inflated
        note = (
            f"Claimed ₹{claimed:,.0f} is {overclaim_pct:.0f}% above the assessed "
            f"fair value of ₹{assessed_mid:,.0f} — strong workshop-inflation "
            f"signal (FS-004). Payout capped at the OEM ceiling ₹{basis:,.0f}; "
            f"₹{disallowed:,.0f} disallowed pending surveyor review."
        )

    out["approved_basis"]       = round(basis)
    out["overclaim_pct"]        = round(overclaim_pct, 1)
    out["overclaim_band"]       = band
    out["overclaim_note"]       = note
    out["disallowed_inflation"]  = round(disallowed)

    # ── Under-claim advisory ───────────────────────────────────────────────────
    # If the customer claimed significantly LESS than the independently assessed
    # value, they will be under-compensated even before deductions. Flag it so
    # the adjudicator can advise the customer to revise their claim upward.
    underclaim_advisory = None
    if overclaim_pct < -(UNDERCLAIM_PCT):
        gap = round(assessed_mid - claimed)
        underclaim_advisory = (
            f"Customer claimed ₹{claimed:,.0f} but the independently assessed "
            f"repair cost is ₹{assessed_mid:,.0f} — the claim is "
            f"₹{gap:,.0f} ({abs(overclaim_pct):.0f}%) below the assessed value. "
            f"The customer may be unaware that deductions (depreciation, deductible) "
            f"will further reduce their payout. Advise the customer to revise their "
            f"claim to the full assessed amount of ₹{round(assessed_max):,.0f} before settlement."
        )
    out["underclaim_advisory"] = underclaim_advisory
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

    # ── Policy add-ons & deductibles ────────────────────────────────────────────
    zero_dep  = _is_zero_dep(policy)
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
    elif zero_dep:
        dep_note = "Zero-Depreciation add-on active — no depreciation deducted."
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

    # ── GST (parts 28% / labour 18%) ───────────────────────────────────────────
    if is_total_loss:
        gst_parts  = 0
        gst_labour = 0
    else:
        parts_value  = repair * PARTS_FRACTION
        labour_value = repair * LABOUR_FRACTION
        gst_parts    = round(parts_value  * GST_PARTS_RATE)
        gst_labour   = round(labour_value * GST_LABOUR_RATE)

    salvage = round(si * 0.05) if is_total_loss else 0

    # ── Net payable ─────────────────────────────────────────────────────────────
    net = round(repair - depreciation - total_ded - salvage + gst_parts + gst_labour)
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
        f"Depreciation: {'Zero-Dep active — waived' if zero_dep else dep_note} | "
        f"Deductibles: compulsory ₹{comp_ded:,} + voluntary ₹{vol_ded:,} | "
        f"GST: 28% on parts, 18% on labour | "
        f"Payout capped at sum insured ₹{si:,}."
    ) if not is_total_loss else (
        f"Total loss settlement: IDV ₹{si:,} − salvage ₹{salvage:,} | "
        f"Deductibles: ₹{total_ded:,} | Payout capped at IDV."
    )

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
        "disallowed_inflation":  assess.get("disallowed_inflation", 0),
        "underclaim_advisory":   assess.get("underclaim_advisory"),
        # Depreciation
        "zero_dep":              zero_dep,
        "vehicle_age_years":     round(age, 1) if age is not None else None,
        "depreciation_pct":      0 if (zero_dep or is_total_loss) else age_dep,
        "depreciation":          depreciation,
        "depreciation_note":     dep_note,
        # Deductibles
        "compulsory_deductible": comp_ded,
        "voluntary_deductible":  vol_ded,
        "total_deductible":      total_ded,
        # GST
        "gst_parts":             gst_parts,
        "gst_on_labour":         gst_labour,
        "gst_rate_pct":          int(GST_LABOUR_RATE * 100),
        # Final
        "salvage_value":         salvage,
        "net_payable":           net,
        "sum_insured_cap":       si,
        "applies":               decision in ("Approve", "Settle"),
        # NCB
        "ncb_pct":               ncb,
        "ncb_advisory":          ncb_advisory,
        "assumptions":           assumptions,
    }
