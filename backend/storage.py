import csv
import io
import json
import os
import threading
from datetime import datetime
from typing import Optional

from PIL import Image

from config import CLAIMS_CSV, CLAIMS_DIR, DATA_DIR, POLICIES_CSV
from services.crypto import decrypt_field, encrypt_field

# CSV columns holding PII — encrypted at rest, decrypted transparently on read.
_PII_FIELDS = ("claimant", "phone")

# Groq's vision API rejects requests over ~4MB; base64 inflates raw bytes by
# ~33%, so cap the on-disk image well below that to leave room for the prompt.
_MAX_IMAGE_BYTES = 2 * 1024 * 1024
_MAX_IMAGE_DIM = 1600

# File-level lock — prevents concurrent investigations clobbering the same CSV row.
_csv_lock = threading.Lock()

CSV_FIELDS = [
    "claim_id", "policy_no", "claimant", "phone", "vehicle",
    "claim_type", "incident_date", "incident_location", "claim_amount",
    "description", "status", "created_at",
    "garage_estimate_amount", "garage_workshop_name", "fir_number",
]

POLICY_FIELDS = [
    "policy_no", "phone", "customer_name", "vehicle_make", "vehicle_model",
    "vehicle_year", "vehicle_reg_no", "coverage_type", "sum_insured",
    "policy_start", "policy_end", "annual_premium",
    "engine_cc", "voluntary_deductible", "ncb_pct",
]


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _ensure_csv():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CLAIMS_CSV):
        with open(CLAIMS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


def get_all_claims() -> list[dict]:
    _ensure_csv()
    with open(CLAIMS_CSV, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for field in _PII_FIELDS:
            if field in row:
                row[field] = decrypt_field(row[field])
    return rows


def get_claim(claim_id: str) -> Optional[dict]:
    for row in get_all_claims():
        if row["claim_id"] == claim_id:
            return row
    return None


def _encrypt_pii_row(row: dict) -> dict:
    row = dict(row)
    for field in _PII_FIELDS:
        if row.get(field):
            row[field] = encrypt_field(row[field])
    return row


def save_claim(claim_data: dict):
    # Hold the same lock as update_claim_field so a concurrent append and
    # full-file rewrite can never interleave and corrupt the CSV.
    with _csv_lock:
        _ensure_csv()
        row = _encrypt_pii_row({k: claim_data.get(k, "") for k in CSV_FIELDS})
        with open(CLAIMS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writerow(row)


def update_claim_field(claim_id: str, field: str, value: str):
    with _csv_lock:
        claims = get_all_claims()  # PII fields come back decrypted here
        for c in claims:
            if c["claim_id"] == claim_id:
                c[field] = value
        with open(CLAIMS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(_encrypt_pii_row(c) for c in claims)


# ── Policy helpers ────────────────────────────────────────────────────────────

def get_all_policies() -> list[dict]:
    if not os.path.exists(POLICIES_CSV):
        return []
    with open(POLICIES_CSV, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_policy_by_phone(phone: str) -> Optional[dict]:
    phone = phone.strip().replace(" ", "").replace("-", "")
    for p in get_all_policies():
        if p.get("phone", "").strip() == phone:
            return p
    return None


# ── Result JSON helpers ───────────────────────────────────────────────────────

def _claim_dir(claim_id: str) -> str:
    return os.path.join(CLAIMS_DIR, claim_id)


def _result_path(claim_id: str) -> str:
    return os.path.join(_claim_dir(claim_id), "result.json")


def get_result(claim_id: str) -> Optional[dict]:
    path = _result_path(claim_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_result(claim_id: str, result: dict):
    os.makedirs(_claim_dir(claim_id), exist_ok=True)
    with open(_result_path(claim_id), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


def update_agent_result(claim_id: str, agent_name: str, data: dict):
    result = get_result(claim_id) or {"claim_id": claim_id, "agents": {}, "summary": {}}
    result["agents"][agent_name] = data
    save_result(claim_id, result)


# ── Human-in-the-loop: adjuster decision & notes ───────────────────────────────

def set_adjuster_decision(claim_id: str, decision: str, adjuster: str, reason: str,
                          ai_decision: str = "") -> dict:
    """Record a human adjuster's final decision on a claim (overriding/confirming AI)."""
    result = get_result(claim_id) or {"claim_id": claim_id, "agents": {}, "summary": {}}
    record = {
        "decision": decision,
        "adjuster": adjuster or "Adjuster",
        "reason": reason or "",
        "ai_decision": ai_decision,
        "overridden": bool(ai_decision) and decision != ai_decision,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    result["adjuster_decision"] = record
    save_result(claim_id, result)
    return record


def add_adjuster_note(claim_id: str, author: str, text: str) -> dict:
    """Append a free-text note to the claim's adjuster note thread."""
    result = get_result(claim_id) or {"claim_id": claim_id, "agents": {}, "summary": {}}
    note = {
        "author": author or "Adjuster",
        "text": text,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    result.setdefault("adjuster_notes", []).append(note)
    save_result(claim_id, result)
    return note


# ── Image helpers ─────────────────────────────────────────────────────────────

def get_claim_images(claim_id: str) -> list[str]:
    images_dir = os.path.join(_claim_dir(claim_id), "images")
    if not os.path.exists(images_dir):
        return []
    return [
        os.path.join(images_dir, f)
        for f in sorted(os.listdir(images_dir))
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]


def delete_claim_image(claim_id: str, filename: str) -> bool:
    """Delete a single image file. Returns True if deleted, False if not found."""
    images_dir = os.path.join(_claim_dir(claim_id), "images")
    # Sanitise: only allow basename, no path traversal
    safe_name = os.path.basename(filename)
    path = os.path.join(images_dir, safe_name)
    if os.path.exists(path) and os.path.isfile(path):
        os.remove(path)
        return True
    return False


def _downscale_image(content: bytes, filename: str) -> tuple[bytes, str]:
    """Resize/recompress an oversized photo so it stays under the vision API's
    request-size limit. A 9MB photo caused Groq 413 "Request Entity Too Large"
    errors on Damage Assessment and Incident Reconstruction in testing —
    base64 encoding inflates raw bytes by ~33% before they're sent to Groq.
    Returns (content, filename) unchanged if already small enough or not a
    decodable image (e.g. a non-image file slipped into the images folder).
    """
    if len(content) <= _MAX_IMAGE_BYTES:
        return content, filename
    try:
        img = Image.open(io.BytesIO(content)).convert("RGB")
        w, h = img.size
        scale = min(1.0, _MAX_IMAGE_DIM / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        data = content
        for quality in (85, 70, 55, 40):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= _MAX_IMAGE_BYTES:
                break
        base, _ = os.path.splitext(filename)
        return data, f"{base}.jpg"
    except Exception:
        return content, filename


def save_uploaded_file(claim_id: str, filename: str, content: bytes, file_type: str = "images") -> str:
    if file_type == "images":
        content, filename = _downscale_image(content, filename)
    dest_dir = os.path.join(_claim_dir(claim_id), file_type)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    with open(dest_path, "wb") as f:
        f.write(content)
    return dest_path


def get_docs_dir(claim_id: str) -> str:
    return os.path.join(_claim_dir(claim_id), "docs")


def get_claim_docs(claim_id: str) -> dict:
    """Return uploaded non-image document paths, keyed by doc_type
    (estimate/fir PDFs, dashcam video, telematics JSON/CSV)."""
    base = get_docs_dir(claim_id)
    result: dict = {}
    for doc_type in ("estimate", "fir", "dashcam", "telematics"):
        d = os.path.join(base, doc_type)
        if os.path.isdir(d):
            result[doc_type] = [
                os.path.join(d, f)
                for f in sorted(os.listdir(d))
                if not f.startswith(".")
            ]
        else:
            result[doc_type] = []
    return result


def generate_claim_id() -> str:
    """Next claim ID for the current month.

    Uses the HIGHEST existing sequence number + 1, scanning BOTH the CSV rows
    and the claim directories on disk. Counting rows alone is unsafe: if a CSV
    row is deleted, the count drops and the next claim would reuse an ID whose
    folder still holds the previous claim's photos/docs/result.json — leaking
    one claimant's evidence into another's case. Taking max+1 over the union of
    CSV IDs and on-disk folders guarantees a never-before-used ID.
    """
    today = datetime.now().strftime("%Y-%m")
    prefix = f"CLM-{today}-"

    used: set[str] = {r["claim_id"] for r in get_all_claims() if r.get("claim_id", "").startswith(prefix)}
    if os.path.isdir(CLAIMS_DIR):
        used.update(name for name in os.listdir(CLAIMS_DIR) if name.startswith(prefix))

    max_seq = 0
    for cid in used:
        try:
            max_seq = max(max_seq, int(cid.rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    return f"{prefix}{max_seq + 1:06d}"
