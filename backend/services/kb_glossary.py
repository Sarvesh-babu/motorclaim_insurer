"""Expands bare knowledge-base reference codes (FI-xxx, FS-xxx, HIST-xxx) into
human-readable {code, type, title, description, relevance} entries, for both
the PDF report and the frontend's KB Sources pills.

Deterministic and Python-authoritative, consistent with the rest of this
codebase's "don't trust the LLM's formatting for transparency data" pattern —
the LLM picks WHICH codes are relevant (kb_references / matched_schemes /
kb_precedents_applied), this module looks up WHAT each code means and, where
possible, WHY it was relevant to this specific claim (by matching it against
this claim's own flagged fraud_checks, whose detail text is already
claim-specific).
"""

import json
import os
import re
import threading

from config import KB_DIR

_INDICATORS_PATH = os.path.join(KB_DIR, "fraud_indicators.json")
_HISTORY_PATH = os.path.join(KB_DIR, "claim_history.json")

_CODE_RE = re.compile(r"\b(FI-[A-Z]+-\d+|FS-\d+|HIST-\d+)\b")

_lock = threading.Lock()
_cache: dict | None = None


def _load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        indicators, schemes, history = [], [], []
        try:
            with open(_INDICATORS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            indicators = d.get("fraud_indicators", [])
            schemes = d.get("known_fraud_schemes", [])
        except Exception:
            pass
        try:
            with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f).get("claims", [])
        except Exception:
            pass
        _cache = {
            "indicators": {i["id"]: i for i in indicators},
            "schemes": {s["scheme_id"]: s for s in schemes},
            "history": {c["case_id"]: c for c in history},
        }
        return _cache


def extract_code(raw: str) -> str | None:
    """Pulls a recognizable KB code out of a string that may be a bare code
    or a longer LLM-formatted phrase (e.g. 'FS-004: Workshop Inflation...')."""
    m = _CODE_RE.search(raw or "")
    return m.group(1) if m else None


def _relevance_from_checks(code: str, fraud_checks: list[dict] | None) -> str | None:
    """If a flagged fraud check's detail mentions this code, that detail IS
    the claim-specific reason it's relevant — reuse it verbatim."""
    if not fraud_checks:
        return None
    for c in fraud_checks:
        if c.get("status") == "flag" and code in (c.get("detail") or ""):
            return c["detail"]
    return None


def expand_code(raw: str, fraud_checks: list[dict] | None = None) -> dict:
    """Expands one raw KB-reference string into a structured entry.
    Falls back to the original string as the title if the code isn't
    recognized (e.g. a free-text scheme name with no embedded code)."""
    kb = _load()
    code = extract_code(raw)

    if code and code.startswith("HIST-") and code in kb["history"]:
        case = kb["history"][code]
        return {
            "code": code,
            "type": "historical_case",
            "title": f"{case.get('vehicle', '')} — {case.get('claim_type', '')}",
            "description": case.get("description", ""),
            "relevance": (
                f"Past case ({case.get('decision', 'Unknown')}, "
                f"fraud {case.get('fraud_label', 'Unknown')}): {case.get('lesson', '')}"
            ),
        }

    if code and code.startswith("FS-") and code in kb["schemes"]:
        scheme = kb["schemes"][code]
        return {
            "code": code,
            "type": "fraud_scheme",
            "title": scheme.get("name", code),
            "description": scheme.get("description", ""),
            "relevance": (
                _relevance_from_checks(code, fraud_checks)
                or f"Matched fraud scheme pattern (risk: {scheme.get('risk_level', 'Unknown')})."
            ),
        }

    if code and code in kb["indicators"]:
        ind = kb["indicators"][code]
        return {
            "code": code,
            "type": "fraud_indicator",
            "title": ind.get("name", code),
            "description": ind.get("description", ""),
            "relevance": (
                _relevance_from_checks(code, fraud_checks)
                or f"Matched fraud indicator (severity: {ind.get('severity', 'Unknown')})."
            ),
        }

    # Unrecognized — surface the raw text as-is so nothing silently disappears.
    return {"code": code, "type": "unknown", "title": raw, "description": "", "relevance": None}


def expand_list(raw_list: list[str] | None, fraud_checks: list[dict] | None = None) -> list[dict]:
    if not raw_list:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for raw in raw_list:
        entry = expand_code(raw, fraud_checks)
        key = entry["code"] or entry["title"]
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out
