"""Local AI-generated-image forensics detector.

Runs a small ONNX vision classifier (haywoodsloan/ai-image-detector-deploy,
served from its community ONNX port LPX55/detection-model-1-ONNX) entirely
on-device via onnxruntime — no torch, no network call at inference time, no
API key. This is a dedicated quantitative signal that sits alongside the
vision LLM's own qualitative authenticity_flags and the EXIF metadata check
in damage_assessment.py: three independent opinions are harder to fool than
one.

Graceful fallback: if the model/libs aren't available (offline first run
with no cached weights, optimum/transformers not installed, etc.) every
function here returns an empty/inert result instead of raising — this signal
is additive, never a hard dependency for the investigation pipeline to run.
"""

import os
import threading
from typing import Any

_MODEL_ID = "LPX55/detection-model-1-ONNX"

# Top-label keywords that indicate "this looks AI-generated/synthetic", since
# the exact label strings are read from the model's own config at load time
# rather than hardcoded (different community ports/versions vary).
_AI_KEYWORDS = ("artificial", "fake", "synthetic", "generated", "ai")
_REAL_KEYWORDS = ("real", "human", "authentic", "photo")

# Below this confidence the model's own call is too close to guess to act on.
_CONFIDENCE_THRESHOLD = 0.75

_lock = threading.Lock()
_state: dict[str, Any] = {"loaded": False, "processor": None, "model": None, "error": None}


def _load():
    """Lazily load the ONNX model + preprocessor once per process."""
    with _lock:
        if _state["loaded"]:
            return
        try:
            from transformers import AutoImageProcessor
            from optimum.onnxruntime import ORTModelForImageClassification

            _state["processor"] = AutoImageProcessor.from_pretrained(_MODEL_ID)
            _state["model"] = ORTModelForImageClassification.from_pretrained(_MODEL_ID)
        except Exception as exc:  # noqa: BLE001 — any failure degrades to "unavailable"
            _state["error"] = str(exc)
        finally:
            _state["loaded"] = True


def is_available() -> bool:
    _load()
    return _state["model"] is not None


def _classify_one(image_path: str) -> dict:
    from PIL import Image

    filename = os.path.basename(image_path)
    entry = {"file": filename, "label": None, "confidence": None,
              "is_ai_generated_suspected": False, "error": None}
    try:
        import numpy as np

        with Image.open(image_path) as img:
            img = img.convert("RGB")
            inputs = _state["processor"](images=img, return_tensors="np")
            outputs = _state["model"](**inputs)
            logits = outputs.logits[0]
            probs = np.exp(logits) / np.exp(logits).sum()
            top_idx = int(probs.argmax())
            label = _state["model"].config.id2label.get(top_idx, str(top_idx))
            confidence = round(float(probs[top_idx]), 4)

        label_lower = label.lower()
        suspected = (
            any(k in label_lower for k in _AI_KEYWORDS)
            and not any(k in label_lower for k in _REAL_KEYWORDS)
            and confidence >= _CONFIDENCE_THRESHOLD
        )
        entry.update(label=label, confidence=confidence, is_ai_generated_suspected=suspected)
    except Exception as exc:  # noqa: BLE001 — one bad image shouldn't kill the batch
        entry["error"] = str(exc)
    return entry


def check_images(image_paths: list[str]) -> list[dict]:
    """Run the local forensics classifier over each image.

    Returns one entry per image: {file, label, confidence, is_ai_generated_suspected, error}.
    Returns [] (no signal, no error raised) if the model isn't available.
    """
    if not image_paths or not is_available():
        return []
    return [_classify_one(p) for p in image_paths]
