"""
ClaimIntel — Explainable Claim Investigation Report (PDF)
Uses fpdf2 (already in requirements). Outputs A4 PDF bytes.

Structure
---------
Page 1 : Executive Summary — claim details + decision banner + summary checks
Page 2 : Agent Findings    — all 5 agents' key outputs
Page 3 : Decision Rationale — decision_reasons + full reasoning trail + KB
"""

from datetime import datetime
from fpdf import FPDF


# ── Latin-1 sanitiser ─────────────────────────────────────────────────────────

def _s(val) -> str:
    """Convert any value to a Latin-1-safe string for fpdf2."""
    if val is None:
        return ""
    text = str(val)
    _MAP = {
        "₹": "Rs.", "₹": "Rs.",
        "—": "-",   "–": "-",
        "‘": "'",   "’": "'",
        "“": '"',   "”": '"',
        "•": "*",   "●": "*",
        "✓": "OK",  "✗": "X",
        "⚠": "!",   "→": "->",
        "°": "deg", "±": "+/-",
    }
    for k, v in _MAP.items():
        text = text.replace(k, v)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _inr(val) -> str:
    try:
        n = int(float(val or 0))
        # Manual Indian number format
        s = str(n)
        if len(s) <= 3:
            return f"Rs. {s}"
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        groups.reverse()
        formatted = ",".join(groups) + "," + last3
        return f"Rs. {formatted}"
    except Exception:
        return "Rs. 0"


def _pct(val) -> str:
    try:
        return f"{int(float(val or 0))}%"
    except Exception:
        return "—"


# ── Colour constants (RGB) ────────────────────────────────────────────────────

C_BG_PAGE   = (248, 250, 252)   # very light grey page bg
C_DARK      = (15,  23,  42)    # near-black text
C_MID       = (51,  65,  85)    # slate-700 for lines
C_MUTED     = (100, 116, 139)   # slate-500 secondary text
C_WHITE     = (255, 255, 255)
C_SLATE_HDR = (30,  41,  59)    # slate-800 section headers
C_APPROVE   = (22,  163,  74)   # green-600
C_REJECT    = (220,  38,  38)   # red-600
C_ESCALATE  = (217, 119,   6)   # amber-600
C_SEVERE    = (220,  38,  38)
C_MODERATE  = (217, 119,   6)
C_MINOR     = (22,  163,  74)
C_INDIGO    = (79,  70,  229)   # indigo-600
C_ROW_ALT   = (241, 245, 249)   # slate-100 alternate row

DECISION_COLOR = {"Approve": C_APPROVE, "Reject": C_REJECT, "Escalate": C_ESCALATE}
SEV_COLOR      = {"Severe": C_SEVERE, "Moderate": C_MODERATE, "Minor": C_MINOR}
RISK_COLOR     = {
    "High": C_REJECT, "Medium": C_ESCALATE, "Low": C_APPROVE,
    "High Risk": C_REJECT, "Medium Risk": C_ESCALATE, "Low Risk": C_APPROVE,
    "Unknown": C_MUTED,
}
CHECK_ICON = {"High": "PASS", "Medium": "WARN", "Low": "FAIL",
              "Low Risk": "PASS", "Medium Risk": "WARN", "High Risk": "FAIL",
              "Unknown": "N/A"}


# ── PDF class ─────────────────────────────────────────────────────────────────

class ClaimPDF(FPDF):
    def __init__(self, claim_id: str):
        super().__init__()
        self.claim_id = claim_id
        self.set_margins(12, 15, 12)
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        y = self.get_y()
        row_h = 8
        # ClaimIntel brand — left, anchored to same Y
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*C_INDIGO)
        self.set_xy(12, y)
        self.cell(60, row_h, "ClaimIntel", align="L")
        # Report descriptor — right, same Y and same height so both sit on the same baseline
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*C_MUTED)
        self.set_xy(72, y)
        self.cell(126, row_h, _s(f"AI Motor Insurance Investigation Report  |  {self.claim_id}"), align="R")
        # Separator line
        self.set_draw_color(*C_MID)
        self.set_line_width(0.4)
        self.set_y(y + row_h + 1)
        self.line(12, self.get_y(), 198, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-14)
        self.set_draw_color(*C_MID)
        self.line(12, self.get_y(), 198, self.get_y())
        self.ln(1)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*C_MUTED)
        self.cell(0, 5,
                  _s(f"Page {self.page_no()}  |  Confidential - For Insurance Use Only  |  "
                     f"Generated {datetime.now().strftime('%d %b %Y')}"),
                  align="C")


# ── Helper drawing methods ────────────────────────────────────────────────────

def _section_header(pdf: ClaimPDF, title: str):
    """Dark filled section title bar."""
    pdf.set_fill_color(*C_SLATE_HDR)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 7, f"  {_s(title)}", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*C_DARK)
    pdf.ln(2)


def _kv_row(pdf: ClaimPDF, label: str, value: str, alt: bool = False, w_label: float = 42):
    """Label + value row, optionally alternating background."""
    if alt:
        pdf.set_fill_color(*C_ROW_ALT)
    else:
        pdf.set_fill_color(*C_WHITE)
    usable = 186.0
    w_val  = usable - w_label
    # Label
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(w_label, 6, _s(label), fill=True)
    # Value
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*C_DARK)
    pdf.cell(w_val, 6, _s(value), fill=True, new_x="LMARGIN", new_y="NEXT")


def _colored_badge(pdf: ClaimPDF, text: str, rgb: tuple, w: float = 30):
    """Small filled colour badge inline."""
    pdf.set_fill_color(*rgb)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(w, 6, _s(text), fill=True, align="C")
    pdf.set_text_color(*C_DARK)


def _check_row(pdf: ClaimPDF, label: str, value: str):
    """Single investigation summary check row."""
    rgb   = RISK_COLOR.get(value, C_MUTED)
    icon  = CHECK_ICON.get(value, "—")
    pdf.set_fill_color(*C_WHITE)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(80, 6, _s(label), fill=True)
    pdf.set_fill_color(*rgb)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(24, 6, _s(icon), fill=True, align="C")
    pdf.set_fill_color(*C_WHITE)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*rgb)
    pdf.cell(0, 6, _s(f"  {value or 'Unknown'}"), fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*C_DARK)


def _bullet_list(pdf: ClaimPDF, items: list[str], color: tuple = C_DARK):
    """Render a list of strings as bullet points."""
    for item in items:
        if not item:
            continue
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*color)
        pdf.cell(6, 5.5, "*")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*C_DARK)
        # multi_cell for wrapping
        x_before = pdf.get_x()
        pdf.multi_cell(0, 5.5, _s(item), new_x="LMARGIN", new_y="NEXT")


def _numbered_list(pdf: ClaimPDF, items: list[str]):
    for i, item in enumerate(items, 1):
        if not item:
            continue
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*C_INDIGO)
        pdf.cell(8, 5.5, f"{i}.")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*C_DARK)
        pdf.multi_cell(0, 5.5, _s(item), new_x="LMARGIN", new_y="NEXT")


def _score_bar(pdf: ClaimPDF, score: int, color: tuple, w: float = 80):
    """Simple horizontal progress bar."""
    bar_h = 4
    filled = max(0, min(w, w * score / 100))
    y = pdf.get_y()
    x = pdf.get_x()
    # Background
    pdf.set_fill_color(226, 232, 240)   # slate-200
    pdf.rect(x, y, w, bar_h, "F")
    # Filled portion
    pdf.set_fill_color(*color)
    if filled > 0:
        pdf.rect(x, y, filled, bar_h, "F")
    pdf.ln(bar_h + 2)


# ── Main generator ────────────────────────────────────────────────────────────

def generate_claim_report(claim: dict, result: dict) -> bytes:
    """
    Generate a full explainable PDF investigation report.
    Returns raw PDF bytes.
    """
    agents     = result.get("agents", {})
    summary    = result.get("summary", {})
    damage     = agents.get("damage_assessment", {})
    fraud      = agents.get("fraud_intelligence", {})
    recon      = agents.get("incident_reconstruction", {})
    context    = agents.get("context_verification", {})
    settlement = agents.get("settlement_recommendation", {})

    claim_id  = _s(claim.get("claim_id", ""))
    decision  = summary.get("decision", "Pending")
    dec_rgb   = DECISION_COLOR.get(decision, C_MUTED)

    pdf = ClaimPDF(claim_id)

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 1 — Executive Summary
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()

    # ── Report title ──────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*C_DARK)
    pdf.cell(0, 10, "Motor Insurance Claim Investigation Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(0, 5,
             _s(f"AI-generated  |  Powered by ClaimIntel  |  {datetime.now().strftime('%d %B %Y')}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Decision banner ───────────────────────────────────────────────────────
    pdf.set_fill_color(*dec_rgb)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 12, f"  DECISION: {decision.upper()}    |    Settlement: {_inr(summary.get('recommended_settlement', 0))}",
             fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # ── 2-column layout: Claim Details + Investigation Summary ────────────────
    y_start = pdf.get_y()
    col_w   = 91.0
    gap     = 4.0

    # -- Left column: Claim Details --
    pdf.set_xy(12, y_start)
    _section_header(pdf, "Claim Details")

    details = [
        ("Claim ID",    claim.get("claim_id",         "—")),
        ("Policy No.",  claim.get("policy_no",         "—")),
        ("Claimant",    claim.get("claimant",          "—")),
        ("Vehicle",     claim.get("vehicle",           "—")),
        ("Claim Type",  claim.get("claim_type",        "—")),
        ("Date",        claim.get("incident_date",     "—")[:16] if claim.get("incident_date") else "—"),
        ("Location",    claim.get("incident_location", "—")),
        ("AI Estimate", _inr(claim.get("claim_amount", 0))),
        ("Submitted",   claim.get("created_at",        "—")),
        ("Status",      claim.get("status",            "—")),
    ]
    for i, (k, v) in enumerate(details):
        _kv_row(pdf, k, v[:48], alt=(i % 2 == 0), w_label=30)

    y_after_left = pdf.get_y()

    # -- Right column: Investigation Summary --
    pdf.set_xy(12 + col_w + gap, y_start)
    # Save and restore X for right column manually
    right_x = 12 + col_w + gap

    # Section header (right side — draw manually)
    pdf.set_fill_color(*C_SLATE_HDR)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(right_x, y_start)
    pdf.cell(col_w, 7, "  Investigation Summary", fill=True, new_x="LEFT", new_y="NEXT")
    pdf.set_text_color(*C_DARK)
    pdf.ln(2)

    # Fraud score
    fraud_score = int(float(summary.get("fraud_risk_score") or 0))
    fraud_label = summary.get("fraud_risk_label", "Unknown")
    fraud_rgb   = RISK_COLOR.get(fraud_label, C_MUTED)
    pdf.set_x(right_x)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(col_w * 0.5, 5.5, "Fraud Risk Score")
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*fraud_rgb)
    pdf.cell(col_w * 0.5, 5.5, _pct(fraud_score), new_x="LEFT", new_y="NEXT")
    pdf.set_text_color(*C_DARK)

    pdf.set_x(right_x)
    _score_bar(pdf, fraud_score, fraud_rgb, w=col_w - 2)

    pdf.set_x(right_x)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(col_w * 0.45, 5, "Label")
    pdf.cell(col_w * 0.45, 5, "Confidence")
    pdf.ln(5)
    pdf.set_x(right_x)
    _colored_badge(pdf, fraud_label, fraud_rgb, w=col_w * 0.45)
    confidence = summary.get("overall_confidence", 0)
    _colored_badge(pdf, _pct(confidence), C_SLATE_HDR, w=col_w * 0.45)
    pdf.ln(8)

    # 4 check rows
    pdf.set_x(right_x)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(col_w, 5, "Verification Checks", new_x="LEFT", new_y="NEXT")
    pdf.ln(1)

    checks = [
        ("Damage Consistency",   summary.get("damage_consistency",   "Unknown")),
        ("Incident Consistency", summary.get("incident_consistency", "Unknown")),
        ("External Verification",summary.get("external_verification","Unknown")),
        ("Behavioural Analysis", summary.get("behavioural_analysis", "Unknown")),
    ]
    for label, val in checks:
        pdf.set_x(right_x)
        rgb  = RISK_COLOR.get(val, C_MUTED)
        icon = CHECK_ICON.get(val, "—")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*C_MUTED)
        pdf.cell(col_w * 0.58, 5.5, label)
        pdf.set_fill_color(*rgb)
        pdf.set_text_color(*C_WHITE)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(14, 5.5, icon, fill=True, align="C")
        pdf.set_fill_color(*C_WHITE)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*rgb)
        pdf.cell(0, 5.5, f"  {val or 'Unknown'}", new_x="LEFT", new_y="NEXT")
        pdf.set_text_color(*C_DARK)

    # ── Claim description ─────────────────────────────────────────────────────
    pdf.set_y(max(y_after_left, pdf.get_y()) + 4)
    _section_header(pdf, "Incident Description")
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(*C_DARK)
    desc = _s(claim.get("description", "No description provided."))
    pdf.multi_cell(0, 5.5, f'"{desc}"', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 2 — Agent Findings
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*C_DARK)
    pdf.cell(0, 8, "Agent Investigation Findings", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # ── Agent 1: Damage Assessment ────────────────────────────────────────────
    _section_header(pdf, "Agent 1 — Damage Assessment  (Vision AI)")

    parts = damage.get("damaged_parts", [])
    if parts:
        # Table header
        pdf.set_fill_color(*C_SLATE_HDR)
        pdf.set_text_color(*C_WHITE)
        pdf.set_font("Helvetica", "B", 7.5)
        col_widths = [52, 24, 40, 30, 26, 14]
        headers    = ["Part", "Severity", "Estimate (Rs.)", "Repair Type", "Pricing", ""]
        for w, h in zip(col_widths, headers):
            pdf.cell(w, 6, h, fill=True)
        pdf.ln()
        # Rows
        for i, p in enumerate(parts):
            alt = (i % 2 == 0)
            pdf.set_fill_color(*(C_ROW_ALT if alt else C_WHITE))
            sev     = p.get("severity", "")
            sev_rgb = SEV_COLOR.get(sev, C_MUTED)
            est     = p.get("repair_estimate_INR", {})
            est_str = f"{_inr(est.get('min', 0))} - {_inr(est.get('max', 0))}" if isinstance(est, dict) else "—"

            pdf.set_text_color(*C_DARK)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.cell(col_widths[0], 6, _s(p.get("part", "")).replace("_", " "), fill=True)
            # Severity badge
            pdf.set_fill_color(*sev_rgb)
            pdf.set_text_color(*C_WHITE)
            pdf.set_font("Helvetica", "B", 7)
            pdf.cell(col_widths[1], 6, _s(sev), fill=True, align="C")
            # Rest
            pdf.set_fill_color(*(C_ROW_ALT if alt else C_WHITE))
            pdf.set_text_color(*C_DARK)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.cell(col_widths[2], 6, _s(est_str), fill=True)
            pdf.cell(col_widths[3], 6, _s(p.get("repair_type", "—")), fill=True)
            pdf.cell(col_widths[4], 6, _s(p.get("pricing_source", "—")), fill=True)
            pdf.cell(col_widths[5], 6, "", fill=True)
            pdf.ln()
        pdf.ln(2)
    else:
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 5, "No damaged parts identified.", new_x="LMARGIN", new_y="NEXT")

    # Vehicle verification
    pdf.set_font("Helvetica", "", 8)
    vm    = damage.get("vehicle_match_in_image", "—")
    vm_rgb = {"Yes": C_APPROVE, "No": C_REJECT, "Unclear": C_ESCALATE}.get(vm, C_MUTED)
    plate = damage.get("plate_text_in_image") or ("Not visible" if damage.get("registration_plate_visible") is False else "—")
    prex  = "Detected" if damage.get("pre_existing_damage_observed") else "None found"
    prex_rgb = C_REJECT if damage.get("pre_existing_damage_observed") else C_APPROVE

    _kv_row(pdf, "Vehicle Match",    vm,    alt=True)
    _kv_row(pdf, "Plate in Image",   plate, alt=False)
    _kv_row(pdf, "Pre-existing Dmg", prex,  alt=True)
    total_est = damage.get("total_repair_estimate", {})
    if isinstance(total_est, dict):
        est_line = f"{_inr(total_est.get('min', 0))} — {_inr(total_est.get('max', 0))}"
    else:
        est_line = "—"
    _kv_row(pdf, "Total Estimate",   est_line, alt=False)
    if damage.get("notes"):
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(*C_MUTED)
        pdf.multi_cell(0, 5, _s(f"Note: {damage['notes']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*C_DARK)
    pdf.ln(4)

    # ── Agent 2: Fraud Intelligence ───────────────────────────────────────────
    _section_header(pdf, "Agent 2 — Fraud Intelligence  (Rule Engine + AI)")

    fraud_score = int(float(fraud.get("fraud_score") or 0))
    fraud_label = fraud.get("fraud_label", "Unknown")
    fraud_rgb   = RISK_COLOR.get(fraud_label, C_MUTED)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*fraud_rgb)
    pdf.cell(0, 7, f"Fraud Score: {fraud_score}%  ({fraud_label} Risk)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*C_DARK)
    _score_bar(pdf, fraud_score, fraud_rgb, w=160)

    indicators = fraud.get("indicators", [])
    if indicators:
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 5, f"Fraud Indicators Detected ({len(indicators)}):", new_x="LMARGIN", new_y="NEXT")
        _bullet_list(pdf, indicators, color=C_REJECT)

    schemes = fraud.get("matched_schemes", [])
    if schemes:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*C_REJECT)
        pdf.cell(0, 5, "Matched Fraud Schemes:", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*C_DARK)
        _bullet_list(pdf, schemes, color=C_REJECT)

    refs = fraud.get("kb_references", [])
    if refs:
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*C_MUTED)
        pdf.cell(0, 5, _s("KB References: " + "  |  ".join(refs[:10])), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*C_DARK)
    pdf.ln(4)

    # ── Agent 3: Incident Reconstruction ──────────────────────────────────────
    _section_header(pdf, "Agent 3 — Incident Reconstruction  (Vision AI)")

    _kv_row(pdf, "Collision Type",    recon.get("collision_type",   "—"), alt=True)
    _kv_row(pdf, "Impact Direction",  recon.get("impact_direction", "—"), alt=False)
    story_match = recon.get("damage_matches_story")
    story_str   = "Yes" if story_match is True else ("No" if story_match is False else "—")
    _kv_row(pdf, "Damage Matches Story", story_str, alt=True)
    _kv_row(pdf, "Confidence",        _pct(recon.get("confidence", 0)), alt=False)

    if recon.get("reconstruction"):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 5, "Reconstruction Narrative:", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_fill_color(*C_ROW_ALT)
        pdf.multi_cell(0, 5.5, _s(recon["reconstruction"]), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_fill_color(*C_WHITE)

    inconsistencies = recon.get("inconsistencies", [])
    if inconsistencies:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*C_ESCALATE)
        pdf.cell(0, 5, "Inconsistencies Found:", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*C_DARK)
        _bullet_list(pdf, inconsistencies, color=C_ESCALATE)
    pdf.ln(4)

    # ── Agent 4: Context Verification ────────────────────────────────────────
    _section_header(pdf, "Agent 4 — Context Verification  (Geocoding + Policy)")

    loc_ok  = context.get("location_verified", False)
    loc_str = context.get("geocoded_location", claim.get("incident_location", "—"))
    coverage_note = context.get("policy_coverage_note", "—")
    coverage_note = coverage_note.replace("\n", " ").replace("\r", " ").strip()
    if len(coverage_note) > 90:
        coverage_note = coverage_note[:87] + "..."
    _kv_row(pdf, "Location Verified", "Yes" if loc_ok else "No",  alt=True)
    _kv_row(pdf, "Geocoded Location", loc_str[:70],               alt=False)
    _kv_row(pdf, "Weather",           context.get("weather", "—")[:70], alt=True)
    _kv_row(pdf, "Policy Coverage",   coverage_note,              alt=False)
    pdf.ln(4)

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 3 — Decision Rationale
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*C_DARK)
    pdf.cell(0, 8, "Decision Rationale & Reasoning", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # ── Final decision recap ──────────────────────────────────────────────────
    pdf.set_fill_color(*dec_rgb)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 9,
             _s(f"  {decision.upper()}  —  Settlement: {_inr(settlement.get('recommended_settlement', 0))}"
                f"  —  Confidence: {_pct(settlement.get('confidence', 0))}"),
             fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*C_DARK)
    if settlement.get("deductibles_applied"):
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*C_MUTED)
        pdf.cell(0, 5,
                 _s(f"  Deductibles applied: {_inr(settlement['deductibles_applied'])}"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*C_DARK)
    pdf.ln(4)

    # ── Decision reasons ─────────────────────────────────────────────────────
    reasons = settlement.get("decision_reasons", [])
    _section_header(pdf, "Why This Decision? — Plain-Language Explanation")
    if reasons:
        _bullet_list(pdf, reasons, color=dec_rgb)
    else:
        # Fallback: derive from summary
        fb = []
        if summary.get("fraud_risk_score"):
            fb.append(f"Fraud risk score: {_pct(summary['fraud_risk_score'])} ({summary.get('fraud_risk_label','Unknown')} Risk)")
        if summary.get("damage_consistency"):
            fb.append(f"Damage consistency: {summary['damage_consistency']}")
        if summary.get("incident_consistency"):
            fb.append(f"Incident reconstruction consistency: {summary['incident_consistency']}")
        if summary.get("external_verification"):
            fb.append(f"External location verification: {summary['external_verification']}")
        _bullet_list(pdf, fb if fb else ["Insufficient data to generate plain-language reasons."], color=dec_rgb)
    pdf.ln(4)

    # ── Settlement breakdown (IRDAI-aligned itemised math) ───────────────────
    # Mirrors the SettlementBreakdown.jsx panel so the downloadable report
    # reconciles the headline figure with visible line items. .get() throughout
    # so a partial/missing breakdown can never raise.
    bd = (summary.get("settlement_breakdown")
          or settlement.get("settlement_breakdown") or {})
    if bd:
        _section_header(pdf, "Settlement Breakdown (IRDAI-aligned)")
        cap = bd.get("inflation_cap_applied")
        is_tl = bd.get("is_total_loss")

        # Repair basis row — label reflects total-loss / inflation-cap state
        if is_tl:
            basis_label = "IDV (sum insured)"
        elif cap:
            basis_label = "AI Assessed Value (inflation cap applied)"
        else:
            basis_label = "Repair Estimate (garage)"
        _kv_row(pdf, basis_label, _inr(bd.get("repair_estimate")), alt=True)

        if cap:
            _kv_row(pdf,
                    "Garage quote (capped)",
                    f"{_inr(bd.get('amount_claimed'))}  -  {_inr(bd.get('disallowed_inflation'))} disallowed (leakage prevented)")

        if not is_tl:
            _kv_row(pdf, f"Less: Depreciation ({_pct(bd.get('depreciation_pct'))})",
                    f"- {_inr(bd.get('depreciation'))}", alt=True)
        _kv_row(pdf, "Less: Compulsory deductible", f"- {_inr(bd.get('compulsory_deductible'))}")
        if bd.get("voluntary_deductible"):
            _kv_row(pdf, "Less: Voluntary deductible", f"- {_inr(bd.get('voluntary_deductible'))}", alt=True)
        if bd.get("salvage_value"):
            _kv_row(pdf, "Less: Salvage value", f"- {_inr(bd.get('salvage_value'))}")
        if bd.get("gst_included"):
            _kv_row(pdf, f"GST ({_pct(bd.get('gst_rate_pct'))}, incl. in repair total)",
                    _inr(bd.get("gst_included")), alt=True)

        # Net payable — emphasised in the decision colour
        pdf.set_fill_color(*C_ROW_ALT)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*C_MUTED)
        pdf.cell(42, 7, _s("NET PAYABLE"), fill=True)
        pdf.set_text_color(*dec_rgb)
        pdf.cell(144, 7, _s(_inr(bd.get("net_payable"))), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*C_DARK)

        if bd.get("ncb_advisory"):
            pdf.ln(1)
            pdf.set_font("Helvetica", "I", 7.5)
            pdf.set_text_color(*C_ESCALATE)
            pdf.multi_cell(0, 4.5, _s(f"NCB advisory: {bd.get('ncb_advisory')}"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*C_DARK)
        pdf.ln(4)

    # ── Reasoning trail ──────────────────────────────────────────────────────
    trail = settlement.get("reasoning_trail", [])
    if trail:
        _section_header(pdf, f"Agent 5 — Settlement Reasoning Trail  ({len(trail)} Steps)")
        _numbered_list(pdf, trail)
        pdf.ln(4)

    # ── KB precedents ─────────────────────────────────────────────────────────
    precedents = settlement.get("kb_precedents_applied", [])
    if precedents:
        _section_header(pdf, "Knowledge Base Precedents Applied")
        _bullet_list(pdf, precedents, color=C_INDIGO)
        pdf.ln(4)

    # ── Disclaimer ────────────────────────────────────────────────────────────
    pdf.ln(4)
    pdf.set_draw_color(*C_MID)
    pdf.line(12, pdf.get_y(), 198, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(*C_MUTED)
    disclaimer = (
        "DISCLAIMER: This report is generated by an AI system (ClaimIntel) and is intended to assist "
        "human insurance adjudicators. It does not constitute a final legal or contractual decision. "
        "All findings must be reviewed and confirmed by a licensed insurance professional before "
        "any settlement action is taken. AI recommendations are based on probabilistic analysis and "
        "may not reflect all relevant facts."
    )
    pdf.multi_cell(0, 4.5, _s(disclaimer), new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
