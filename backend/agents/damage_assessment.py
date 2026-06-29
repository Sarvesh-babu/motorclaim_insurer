import re
from typing import Any

from PIL import Image, ExifTags

import storage
from agents.base_agent import BaseAgent
from services import forensics_detector
from services.llm_client import ask_json
from services.rag_client import get_vehicle_pricing_context


def _extract_reg_no(vehicle_str: str) -> str:
    """Pull the registration number out of 'Make Model (REG123)' format."""
    m = re.search(r'\(([A-Z0-9 \-]+)\)', vehicle_str.upper())
    if m:
        return re.sub(r'[\s\-]', '', m.group(1))
    return ""

BASE_PROMPT = """You are an expert vehicle damage assessor and fraud detection specialist with access to OEM parts pricing data.
Analyze ALL provided images of a vehicle (which MAY OR MAY NOT be damaged) and produce an accurate, evidence-based damage report along with visual fraud verification signals.

{kb_context}

Claim description: {description}
Registered vehicle (from policy): {vehicle}
Number of images provided: {{image_count}}

━━━ #1 RULE — DO NOT HALLUCINATE DAMAGE (read first) ━━━
- Report a part as damaged ONLY if you can CLEARLY SEE actual physical damage on it in the image.
- An UNDAMAGED vehicle is a VALID and EXPECTED result. If the car looks intact, return an EMPTY
  "damaged_parts" list and set "damage_present": false. That is the CORRECT answer for a clean car —
  do NOT invent damage to fill the report.
- NEVER mistake any of the following for damage:
    reflections, shadows, sun glare, lens flare, dirt, dust, mud splatter, water droplets, wet/rainy
    surfaces, normal panel gaps and trim lines, rubber moulding, badges/logos, number plates, antennas,
    tinted or dark glass, the dark colour of the paint itself, or background objects mirrored on the body.
- A part being VISIBLE is NOT the same as a part being DAMAGED. Seeing a headlight does not mean the
  headlight is damaged. Only report it if it is cracked, broken, shattered, deformed, or detached.
- If you are NOT sure whether something is real damage or just one of the artefacts above → do NOT report it.
- It is far worse to invent damage that is not there than to miss a tiny scratch. WHEN IN DOUBT, LEAVE IT OUT.

━━━ PHOTO AUTHENTICITY (read before assessing damage) ━━━
- Before assessing damage, judge whether each image looks like a REAL photo of a physical vehicle taken
  with a camera/phone, as opposed to AI-generated, a stock/marketing photo, or a photo of a screen/printout.
- Signs of AI-generation: unnaturally smooth or plastic-looking surfaces, inconsistent or impossible
  shadows/reflections, warped or nonsensical text on plates/badges, asymmetric or melted-looking parts,
  an unnaturally "perfect" studio background for a claimed roadside/accident scene.
- Signs of a stock/marketing photo: pristine showroom lighting, watermarks/logos, generic empty backdrop,
  composition that looks like an advertisement rather than a casual damage photo.
- Signs of a screenshot/photo-of-a-screen: visible screen bezels, moiré pattern, glare from a display,
  UI elements (browser chrome, app icons) visible in frame.
- Only raise a flag when you see a CONCRETE visual tell — do not flag an image merely for being
  well-lit or high quality. A genuine clear phone photo is not suspicious by itself.

━━━ BOUNDING BOX RULES (read carefully) ━━━
- Coordinates are PERCENTAGE-BASED: x,y = top-left corner (0–100%), w,h = width/height (0–100%)
- image_index is 0-based: 0 = first image, 1 = second image, etc.
- Each image is preceded by a text label "[Image 0]", "[Image 1]", … — set
  "image_index" to the number on the label of the image the damage appears in.
  Damage seen in the second image MUST use "image_index": 1, not 0.
- The box MUST tightly enclose only the damaged region — NOT the whole vehicle or whole panel
- x + w ≤ 100 and y + h ≤ 100 at all times; w and h must each be at least 5
- A tight box for a bumper in the lower-left would be: {{"image_index": 0, "x": 5, "y": 62, "w": 38, "h": 22}}
- A tight box for a headlight on the right side would be: {{"image_index": 0, "x": 68, "y": 30, "w": 20, "h": 18}}

━━━ DAMAGE SCAN (inspect every zone — but report ONLY what is clearly damaged) ━━━
Systematically inspect every visible component, then report a zone ONLY if it shows real, visible damage:
  Front zone : front bumper, grille, hood/bonnet, headlights (L+R), foglights, tow hook cover
  Side zone  : all doors, side mirrors, sills/rockers, fenders (front+rear), wheel arches, A/B/C pillars
  Rear zone  : rear bumper, boot/tailgate, tail lights (L+R), reverse lights, exhaust tip
  Glass/roof : windshield, all side windows, rear glass, sunroof panel, roof panel, wiper blades
  Wheels     : all 4 tyres (check bulges/flats), alloy/steel rims (kerb damage), wheel arch liners
  Underbody  : report if visible — suspension, skid plates, exhaust
Genuine damage — when ACTUALLY VISIBLE — should be reported, including clear scratches/scuffs, paint
transfer, misaligned panels, cracked/broken lights or glass, and dents or creases. But every part you
report MUST have concrete, describable damage you can point to. Do not report a part just because it
exists in the photo.

━━━ OTHER RULES ━━━
- DO NOT estimate repair costs in rupees — a separate pricing engine computes all costs.
  Your job is to identify each damaged part, its severity, confidence, and the correct repair_type.
- For EACH damaged part you MUST provide:
    "confidence": High = unmistakable, clearly visible damage; Medium = likely damage but angle/lighting
                  limits certainty; Low = you suspect damage but cannot clearly confirm it.
    "visible_evidence": one short phrase naming the EXACT damage and where, e.g.
                  "deep horizontal scrape across lower front bumper, paint missing". If you cannot
                  describe concrete visible damage here, DELETE the part — do not report it.
- repair_type rules: "Replace" if the part is broken/shattered/crushed/torn or severely deformed;
  "Repair" for dents/creases that can be hammered and repainted; "Paint" for scratches/scuffs only.
  Glass, lights, mirrors and wheels that are damaged are always "Replace".
- CONSISTENCY: If the description claims an accident/damage but you see NO damage, set
  "consistent_with_description": "Low" and explain in notes. If you see damage matching the story, "High".
- PLATE RULE: Set registration_plate_visible only if the plate text is physically legible in the image.
  Do NOT guess or copy the plate from the claim description. If unclear → false and null.
- Return ONLY valid JSON — no markdown fences, no explanation text

Return a JSON object with exactly these fields:
{{
  "damage_present": true,
  "no_damage_reason": null,
  "damaged_parts": [
    {{
      "part": "snake_case part name e.g. front_bumper, headlight_left, door_rear_right",
      "severity": "Minor|Moderate|Severe",
      "confidence": "High|Medium|Low",
      "visible_evidence": "exact damage seen and where",
      "bounding_box": {{"image_index": 0, "x": 10, "y": 20, "w": 30, "h": 25}},
      "repair_type": "Replace|Repair|Paint"
    }}
  ],
  "overall_severity": "None|Minor|Moderate|Severe",
  "total_repair_estimate": {{"min": 0, "max": 0}},
  "consistent_with_description": "High|Medium|Low",
  "notes": "Brief assessment including any pricing basis observations",
  "vehicle_match_in_image": "Yes|No|Unclear",
  "vehicle_seen_description": "Describe make/model/color visible in images",
  "registration_plate_visible": false,
  "plate_text_in_image": null,
  "pre_existing_damage_observed": false,
  "pre_existing_damage_notes": null,
  "multiple_vehicles_in_frame": false,
  "image_quality_flags": ["any of: blurry, too_dark, overexposed, too_far, partial_view, low_resolution — or 'none' if photos are clear and usable"],
  "authenticity_flags": ["any of: ai_generated_suspected, stock_photo_suspected, manipulated_suspected, screenshot_suspected — or 'none' if photos look like genuine camera photos"],
  "authenticity_notes": "brief reason for any authenticity flag raised, or null if none",
  "status": "completed",
  "summary": "Severity: X | Est. Repair: ₹X–₹X | Parts: N damaged | Vehicle Match: Yes/No/Unclear"
}}

If the vehicle is undamaged: set "damage_present": false, "damaged_parts": [], "overall_severity": "None",
"no_damage_reason" to a short explanation, and make "summary" say "No visible damage detected".
"""


class DamageAssessmentAgent(BaseAgent):
    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        claim = context["claim"]
        vehicle = claim.get("vehicle", "")
        image_paths = storage.get_claim_images(claim["claim_id"])

        # RAG: fetch vehicle parts pricing from knowledge base
        kb_context = get_vehicle_pricing_context(vehicle)

        image_count = len(image_paths) if image_paths else 0
        prompt = BASE_PROMPT.replace("{{image_count}}", str(image_count)).format(
            kb_context=kb_context,
            description=claim.get("description", ""),
            vehicle=vehicle,
        )
        result = ask_json(prompt, image_paths if image_paths else None,
                          agent_name="damage_assessment", claim_id=claim["claim_id"])
        result.setdefault("status", "completed")

        # ── Plate verification ────────────────────────────────────────────────
        if not image_paths:
            # No images submitted — plate cannot be verified at all
            result["registration_plate_visible"] = False
            result["plate_text_in_image"] = None

        # ── No-damage guard + low-confidence filter (anti-hallucination) ──────
        # The vision model can invent phantom damage on a clean car (reflections,
        # shadows, dark paint). We (a) quarantine Low-confidence detections so we
        # never PRICE a guess, and (b) honour an explicit "no damage" verdict so
        # an undamaged vehicle yields a zero estimate, not a fabricated one.
        self._apply_no_damage_guard(result, bool(image_paths))

        # ── Validate and clamp every bounding box ─────────────────────────────
        max_img_idx = max(image_count - 1, 0)
        for part in result.get("damaged_parts", []):
            bb = part.get("bounding_box")
            if not isinstance(bb, dict):
                part["bounding_box"] = {"image_index": 0, "x": 5, "y": 5, "w": 30, "h": 30}
                continue
            # Clamp image_index to valid range
            idx = bb.get("image_index", 0)
            if not isinstance(idx, int) or idx < 0 or idx > max_img_idx:
                bb["image_index"] = 0
            # Clamp x, y, w, h to [0, 100] and ensure minimum size of 5
            x = max(0.0, min(100.0, float(bb.get("x") or 0)))
            y = max(0.0, min(100.0, float(bb.get("y") or 0)))
            w = max(5.0, min(100.0, float(bb.get("w") or 10)))
            h = max(5.0, min(100.0, float(bb.get("h") or 10)))
            # Ensure box doesn't overflow image boundaries
            if x + w > 100:
                w = 100 - x
            if y + h > 100:
                h = 100 - y
            bb["x"], bb["y"], bb["w"], bb["h"] = round(x, 1), round(y, 1), round(w, 1), round(h, 1)

        # ── Deterministic pricing engine ──────────────────────────────────────
        # The vision model only classifies (part + severity + repair_type).
        # The engine computes all rupee amounts from a segment-aware catalog,
        # refined by live web prices when an API key is configured. This OVERRIDES
        # any prices the LLM guessed, so estimates are reproducible and defensible.
        try:
            from services.pricing_engine import estimate_damage
            pricing = estimate_damage(vehicle, result.get("damaged_parts", []), live=True)
            result["total_repair_estimate"] = pricing["total_repair_estimate"]
            result["vehicle_segment"] = pricing["segment"]
            result["pricing_method"] = pricing["pricing_method"]
            result["pricing_sources"] = pricing["pricing_sources"]
            # Regenerate the summary string now that we have real prices.
            # The LLM's summary said ₹0–₹0 because it was told not to price.
            est   = pricing["total_repair_estimate"]
            lo, hi = est.get("min", 0), est.get("max", 0)
            n_parts = len(result.get("damaged_parts") or [])
            match   = result.get("vehicle_match_in_image", "Unclear")
            sev     = result.get("overall_severity", "Unknown")
            result["summary"] = (
                f"Severity: {sev} | Est. Repair: "
                f"₹{lo:,}–₹{hi:,} | "
                f"Parts: {n_parts} damaged | Vehicle Match: {match}"
            )
        except Exception as e:
            result["pricing_method"] = f"unavailable ({e})"

        # Garage estimate cross-check
        # Prefer manually entered amount; fall back to parsed estimate doc
        garage_amount = 0.0
        try:
            garage_amount = float(claim.get("garage_estimate_amount") or 0)
        except (ValueError, TypeError):
            pass

        if garage_amount == 0:
            parsed_est = (context.get("docs") or {}).get("estimate") or {}
            try:
                garage_amount = float(parsed_est.get("total_estimate_inr") or 0)
            except (ValueError, TypeError):
                pass

        est = result.get("total_repair_estimate", {})
        ai_min = float(est.get("min", 0) if isinstance(est, dict) else 0)
        ai_max = float(est.get("max", 0) if isinstance(est, dict) else 0)
        ai_mid = (ai_min + ai_max) / 2 if ai_max > 0 else 0

        if garage_amount > 0 and ai_mid > 0:
            variance_pct = ((garage_amount - ai_mid) / ai_mid) * 100
            result["garage_estimate_provided"] = True
            result["garage_estimate_amount_inr"] = garage_amount
            result["garage_vs_ai_variance_pct"] = round(variance_pct, 1)
            result["garage_inflation_flag"] = variance_pct > 40
            direction = "higher" if variance_pct > 0 else "lower"
            result["garage_inflation_note"] = (
                f"Garage estimate ₹{garage_amount:,.0f} is {abs(variance_pct):.0f}% {direction} "
                f"than AI mid-estimate ₹{ai_mid:,.0f}"
            )
        else:
            result["garage_estimate_provided"] = garage_amount > 0
            result["garage_estimate_amount_inr"] = garage_amount
            result["garage_vs_ai_variance_pct"] = None
            result["garage_inflation_flag"] = False
            result["garage_inflation_note"] = None

        # ── Image quality gate (#6 — data quality) ────────────────────────────
        # Combine deterministic checks (photo count, plate legibility) with the
        # vision model's own quality flags (blur, darkness, framing), EXIF
        # metadata, and a dedicated local forensics classifier — three
        # independent authenticity opinions are harder to fool than one.
        exif_signals = self._exif_authenticity_check(image_paths)
        forensics_signals = forensics_detector.check_images(image_paths)
        result["image_quality"] = self._image_quality_gate(
            result, image_count, exif_signals, forensics_signals)

        return result

    @staticmethod
    def _apply_no_damage_guard(result: dict, has_images: bool) -> None:
        """Reconcile the model's damage list against its own confidence + the
        explicit no-damage flag, mutating `result` in place.

        - Low-confidence parts are moved to `low_confidence_observations` (kept
          for the adjuster, but NOT drawn or priced) so a guess never becomes a payout.
        - If nothing confident remains (or the model said so), the vehicle is treated
          as undamaged: empty parts, zero estimate, severity "None".
        """
        parts = result.get("damaged_parts")
        if not isinstance(parts, list):
            parts = []

        kept, low_conf = [], []
        for p in parts:
            if not isinstance(p, dict):
                continue
            if str(p.get("confidence", "")).strip().lower() == "low":
                low_conf.append(p)
            else:
                kept.append(p)

        result["damaged_parts"] = kept
        if low_conf:
            result["low_confidence_observations"] = low_conf

        # Reconcile the explicit flag with what actually survived filtering.
        damage_present = result.get("damage_present")
        if damage_present is None:
            damage_present = len(kept) > 0
        if not kept:
            damage_present = False
        result["damage_present"] = damage_present

        if not damage_present:
            result["damaged_parts"] = []
            result["overall_severity"] = "None"
            result["total_repair_estimate"] = {"min": 0, "max": 0}
            if not result.get("no_damage_reason"):
                result["no_damage_reason"] = (
                    "No clearly visible damage detected in the submitted photos."
                    if has_images else "No photos submitted — damage could not be assessed."
                )
            result["summary"] = (
                "No visible damage detected" if has_images
                else "No photos submitted — damage not assessed"
            )

    @staticmethod
    def _exif_authenticity_check(image_paths: list[str]) -> list[dict]:
        """Inspect EXIF metadata per image as a soft authenticity signal.

        Genuine phone/camera photos almost always carry SOME EXIF data (camera
        make/model, capture timestamp). AI-generated images and many downloaded
        stock photos carry none at all. A screenshot or a re-saved/edited photo
        can also strip EXIF, so an empty result is a SOFT signal, not proof —
        it's combined with the vision model's own authenticity_flags, never
        used alone to block a claim.
        """
        signals = []
        for path in image_paths:
            entry = {"file": path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                      "has_exif": False, "has_camera_model": False,
                      "has_capture_timestamp": False, "has_gps": False}
            try:
                with Image.open(path) as img:
                    exif = img.getexif()
                    if exif:
                        entry["has_exif"] = True
                        tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
                        entry["has_camera_model"] = bool(tags.get("Model"))
                        entry["has_capture_timestamp"] = bool(
                            tags.get("DateTime") or tags.get("DateTimeOriginal")
                        )
                        entry["has_gps"] = bool(exif.get_ifd(0x8825)) if hasattr(exif, "get_ifd") else False
            except Exception:
                pass
            signals.append(entry)
        return signals

    @staticmethod
    def _image_quality_gate(result: dict, photo_count: int, exif_signals: list[dict] | None = None,
                             forensics_signals: list[dict] | None = None) -> dict:
        issues: list[str] = []
        blocking = False  # blocking issues should hold the claim for resubmission

        if photo_count == 0:
            issues.append("No damage photos submitted")
            blocking = True
        elif photo_count == 1:
            issues.append("Only 1 photo provided — at least 2 angles recommended")

        # Plate legibility (already validated above against the actual images)
        if photo_count > 0 and result.get("registration_plate_visible") is False:
            issues.append("Registration plate not legible in any photo")

        # Vision-model quality flags
        _LABELS = {
            "blurry":         "Photos appear blurry",
            "too_dark":       "Photos are too dark",
            "overexposed":    "Photos are overexposed",
            "too_far":        "Photos taken too far from the damage",
            "partial_view":   "Damage only partially visible in frame",
            "low_resolution": "Photo resolution too low for assessment",
        }
        llm_flags = result.get("image_quality_flags") or []
        if isinstance(llm_flags, str):
            llm_flags = [llm_flags]
        seen_blocking_visual = False
        for f in llm_flags:
            key = str(f).strip().lower()
            if key and key != "none" and key in _LABELS:
                issues.append(_LABELS[key])
                if key in ("blurry", "too_dark", "overexposed", "low_resolution"):
                    seen_blocking_visual = True

        if seen_blocking_visual and photo_count > 0:
            blocking = True

        # ── Photo authenticity (AI-generated / stock / manipulated images) ────
        _AUTH_LABELS = {
            "ai_generated_suspected": "Photo may be AI-generated",
            "stock_photo_suspected":  "Photo may be a stock/marketing image, not a genuine damage photo",
            "manipulated_suspected":  "Photo may have been digitally manipulated",
            "screenshot_suspected":   "Photo appears to be a screenshot/photo-of-a-screen, not a direct camera photo",
        }
        auth_flags_raw = result.get("authenticity_flags") or []
        if isinstance(auth_flags_raw, str):
            auth_flags_raw = [auth_flags_raw]
        auth_flags = [str(f).strip().lower() for f in auth_flags_raw
                      if str(f).strip().lower() not in ("", "none")]

        exif_signals = exif_signals or []
        exif_suspect_count = sum(1 for s in exif_signals if not s.get("has_exif"))
        exif_signal_summary = (
            f"{exif_suspect_count}/{len(exif_signals)} photo(s) have no EXIF metadata "
            f"(camera/timestamp data) — common in AI-generated or downloaded images"
        ) if exif_signals and exif_suspect_count > 0 else None

        review_recommended = bool(auth_flags) or (
            exif_signals and exif_suspect_count == len(exif_signals)
        )

        # Local forensics classifier (dedicated AI-image-detector model, run
        # on-device) — a third, independent authenticity opinion alongside
        # the vision LLM's judgment and the EXIF metadata check above.
        forensics_signals = forensics_signals or []
        forensics_suspects = [s for s in forensics_signals if s.get("is_ai_generated_suspected")]
        forensics_summary = (
            f"Local forensics model flagged {len(forensics_suspects)}/{len(forensics_signals)} "
            f"photo(s) as likely AI-generated (confidence "
            f"{max((s['confidence'] for s in forensics_suspects), default=0):.0%})"
        ) if forensics_suspects else None

        if forensics_suspects:
            review_recommended = True

        if auth_flags:
            for key in auth_flags:
                issues.append(_AUTH_LABELS.get(key, f"Authenticity concern: {key}"))
            blocking = True
        elif exif_signal_summary and exif_suspect_count == len(exif_signals):
            # ALL photos missing EXIF is a stronger signal than a mix (mixed is
            # normal — apps/messaging often strip EXIF from some transfers).
            issues.append(exif_signal_summary)
            blocking = True

        if forensics_summary:
            issues.append(forensics_summary)
            blocking = True

        return {
            "photo_count":          photo_count,
            "plate_visible":        bool(result.get("registration_plate_visible")),
            "issues":               issues,
            "gate_passed":          not blocking,
            "resubmit_recommended": blocking and not review_recommended,
            "authenticity": {
                "flags":             auth_flags or ["none"],
                "notes":             result.get("authenticity_notes"),
                "exif_signals":      exif_signals,
                "forensics_signals": forensics_signals,
                "review_recommended": review_recommended,
            },
        }
