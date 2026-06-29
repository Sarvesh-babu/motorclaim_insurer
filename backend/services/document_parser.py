"""
Document parsing service — extracts structured data from garage estimate
and FIR documents (PDF or image) using the vision LLM.

Parsed results are stored as:
  data/claims/{claim_id}/docs/parsed_estimate.json
  data/claims/{claim_id}/docs/parsed_fir.json
"""

import json
import os
from typing import Optional

import storage

_ESTIMATE_PROMPT = """You are a motor insurance fraud analyst reviewing a garage repair estimation slip.
Extract all available data from this document image.

The claim is for vehicle: {vehicle}

Return a JSON object with exactly these fields:
{{
  "doc_type": "garage_estimate",
  "workshop_name": "Workshop name or null",
  "workshop_address": "Address or null",
  "gstin": "GSTIN number if present or null",
  "total_estimate_inr": <total amount as number, 0 if not found>,
  "line_items": [
    {{"part": "Part/service name", "type": "Replace|Repair|Paint|Labour", "amount": <number>}}
  ],
  "date_issued": "YYYY-MM-DD or null",
  "vehicle_reg_mentioned": "Registration number from document or null",
  "parsed_ok": <true if document was readable and data extracted, false if unreadable>
}}"""

_FIR_PROMPT = """You are a motor insurance fraud analyst reviewing a First Information Report (FIR).
Extract all available data from this FIR document image.

Return a JSON object with exactly these fields:
{{
  "doc_type": "fir",
  "fir_number": "FIR/case number or null",
  "police_station": "Police station name and location or null",
  "date_filed": "YYYY-MM-DD or null",
  "incident_date_in_fir": "YYYY-MM-DD or null",
  "incident_location_in_fir": "Incident location as stated in FIR or null",
  "vehicle_reg_in_fir": "Vehicle registration number mentioned in FIR or null",
  "complainant_name_in_fir": "Complainant name or null",
  "parsed_ok": <true if document was readable and data extracted, false if unreadable>
}}"""


def _pdf_to_images(pdf_path: str) -> list[str]:
    """Convert PDF pages to temp PNG image files. Returns paths (caller must delete)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []
    image_paths: list[str] = []
    try:
        doc = fitz.open(pdf_path)
        for i in range(len(doc)):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=150)
            out = f"{pdf_path}_page{i}.png"
            pix.save(out)
            image_paths.append(out)
        doc.close()
    except Exception:
        pass
    return image_paths


def parse_document(claim_id: str, file_path: str, doc_type: str, claim: dict) -> dict:
    """
    Parse one document file with the vision LLM.
    Writes parsed_estimate.json or parsed_fir.json under docs/.
    Returns the parsed dict.
    """
    from services.llm_client import ask_json

    ext = os.path.splitext(file_path)[-1].lower().lstrip(".")
    temp_images: list[str] = []

    if ext == "pdf":
        image_paths = _pdf_to_images(file_path)
        temp_images = image_paths
    else:
        image_paths = [file_path] if os.path.exists(file_path) else []

    if not image_paths:
        result: dict = {"doc_type": doc_type, "parsed_ok": False, "error": "No readable image/PDF"}
    else:
        vehicle = claim.get("vehicle", "")
        prompt = (
            _ESTIMATE_PROMPT.format(vehicle=vehicle)
            if doc_type == "estimate"
            else _FIR_PROMPT
        )
        try:
            result = ask_json(prompt, image_paths)
            result.setdefault("parsed_ok", True)
            result.setdefault("doc_type", doc_type)
        except Exception as exc:
            result = {"doc_type": doc_type, "parsed_ok": False, "error": str(exc)}

    # Clean up temp images from PDF conversion
    for p in temp_images:
        try:
            os.remove(p)
        except Exception:
            pass

    # Persist to docs/
    docs_dir = storage.get_docs_dir(claim_id)
    os.makedirs(docs_dir, exist_ok=True)
    out_path = os.path.join(docs_dir, f"parsed_{doc_type}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)

    return result


def get_parsed_doc(claim_id: str, doc_type: str) -> Optional[dict]:
    """Load a previously parsed document. Returns None if not yet parsed."""
    path = os.path.join(storage.get_docs_dir(claim_id), f"parsed_{doc_type}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
