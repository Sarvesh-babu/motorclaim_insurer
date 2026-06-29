"""
LLM client for ClaimIntel — powered by Groq (free, fast, open-source models).

Text agent calls  → llama-3.3-70b-versatile  (6,000 req/day free)
Vision agent calls → meta-llama/llama-4-scout-17b-16e-instruct (1,000 req/day free)

Public API (kept stable across the original Gemini → Groq migration):
  ask_text(prompt) -> str
  ask_with_images(prompt, image_paths) -> str
  ask_json(prompt, image_paths=None) -> dict
"""

import base64
import json
import re
import time

from groq import Groq

from config import GROQ_API_KEY
from services import token_tracker

_client = Groq(api_key=GROQ_API_KEY)

TEXT_MODEL   = "llama-3.3-70b-versatile"                      # text-only agents
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"    # vision agents (A1, A3)

_TEXT_MAX_TOKENS   = 4096
_VISION_MAX_TOKENS = 8192   # damage reports with many parts can exceed 4096

_MAX_RETRIES = 2
_RETRY_DELAY = 5  # seconds between retries

# Groq's vision endpoint error says "supports up to 5 images" but rejected a
# request with exactly 5 in testing (off-by-one in their enforcement) — capping
# at 4 to stay clear of the boundary. Callers (e.g. incident_reconstruction
# combining damage photos + dashcam frames) may exceed this, so it's enforced
# centrally here rather than relying on every caller to budget correctly.
_MAX_VISION_IMAGES = 4


# ── Image helpers ─────────────────────────────────────────────────────────────

def _b64_image(path: str) -> tuple[str, str]:
    """Return (base64_string, mime_type) for an image file."""
    ext = path.rsplit(".", 1)[-1].lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif",
            "webp": "image/webp"}.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8"), mime


# ── Core LLM call with retry ──────────────────────────────────────────────────

def _call(messages: list[dict], model: str, agent_name: str | None = None,
          claim_id: str | None = None) -> str:
    """Call Groq with retry on transient errors. Logs token usage (observability)."""
    max_tokens = _VISION_MAX_TOKENS if model == VISION_MODEL else _TEXT_MAX_TOKENS
    last_err = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = _client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                max_tokens=max_tokens,
            )
            usage = getattr(resp, "usage", None)
            if usage is not None:
                token_tracker.log_usage(
                    claim_id, agent_name, model,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    total_tokens=getattr(usage, "total_tokens", 0) or 0,
                )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)
    raise RuntimeError(f"LLM call failed after {_MAX_RETRIES + 1} attempts: {last_err}")


# ── Public API (same interface as the original Gemini wrapper) ────────────────
# agent_name/claim_id are optional and only used for token-usage observability —
# omitting them just means the usage row is logged without that attribution.

def ask_text(prompt: str, agent_name: str | None = None, claim_id: str | None = None) -> str:
    messages = [{"role": "user", "content": prompt}]
    return _call(messages, TEXT_MODEL, agent_name, claim_id)


def ask_with_images(prompt: str, image_paths: list[str], agent_name: str | None = None,
                     claim_id: str | None = None) -> str:
    if len(image_paths) > _MAX_VISION_IMAGES:
        image_paths = image_paths[:_MAX_VISION_IMAGES]
    content: list[dict] = [{"type": "text", "text": prompt}]
    for i, path in enumerate(image_paths):
        b64, mime = _b64_image(path)
        # Precede each image with a 0-based text label so the vision model can
        # reliably reference it by index (e.g. bounding_box.image_index) on
        # multi-image claims — otherwise it cannot tell which image a part is in.
        content.append({"type": "text", "text": f"[Image {i}]"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    messages = [{"role": "user", "content": content}]
    return _call(messages, VISION_MODEL, agent_name, claim_id)


def _clean_raw(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$",           "", raw, flags=re.MULTILINE)
    return raw.strip()


def _repair_json(text: str) -> str:
    """Fix the most common LLM JSON mistakes."""
    # Trailing commas before } or ]
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    # Smart/curly quotes → straight quotes
    for bad, good in [("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")]:
        text = text.replace(bad, good)
    return text


def _try_parse(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def ask_json(prompt: str, image_paths: list[str] | None = None,
             agent_name: str | None = None, claim_id: str | None = None) -> dict:
    full_prompt = prompt + "\n\nRespond ONLY with valid JSON. No markdown fences, no explanation."
    raw = (ask_with_images(full_prompt, image_paths, agent_name, claim_id) if image_paths
           else ask_text(full_prompt, agent_name, claim_id))
    raw = _clean_raw(raw)

    # Pass 1: direct parse
    result = _try_parse(raw)
    if result is not None:
        return result

    # Pass 2: extract outermost {...} block
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        extracted = match.group()

        # Pass 3: repair common syntax errors
        for candidate in (extracted, _repair_json(extracted)):
            result = _try_parse(candidate)
            if result is not None:
                return result

    # Pass 4: ask the text model to re-emit clean JSON (handles missing commas etc.)
    repair_prompt = (
        "The text below is a JSON object with syntax errors (missing commas, bad quotes, etc.).\n"
        "Return ONLY the corrected valid JSON — no explanation, no markdown fences:\n\n"
        + raw[:4000]
    )
    fixed = _clean_raw(ask_text(repair_prompt, agent_name, claim_id))
    result = _try_parse(fixed)
    if result is not None:
        return result
    match2 = re.search(r"\{.*\}", fixed, re.DOTALL)
    if match2:
        result = _try_parse(_repair_json(match2.group()))
        if result is not None:
            return result

    raise ValueError(
        f"ask_json: could not parse response after 4 repair passes.\nRaw start: {raw[:300]}"
    )
