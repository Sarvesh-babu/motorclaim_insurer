from typing import Any

import storage
from agents.base_agent import BaseAgent
from services.llm_client import ask_json
from services.rag_client import get_similar_cases_context

BASE_PROMPT = """You are an accident reconstruction expert with access to a database of historical claim cases.
Analyze the vehicle damage images and claim narrative to reconstruct the incident.
Use historical precedents from the knowledge base to calibrate your confidence assessment.

{kb_context}

Claim description: {description}
Vehicle: {vehicle}
Claim type: {claim_type}
Damage map (from Agent 1): {damage_map}
Telematics/IoT sensor data (if provided): {telematics_summary}
{image_note}

Instructions:
- The damage map shows parts damaged and their severity from the AI damage assessment
- Historical precedents (if provided above) show how similar damage patterns were classified
- If telematics data is provided, ground your reconstruction in it — e.g. a claimed high-speed
  collision with no hard-braking/impact reading is a red flag worth noting in inconsistencies
- If dashcam frames are provided, they are in CHRONOLOGICAL ORDER — use the sequence to reason
  about causality (what happened first, point of impact, aftermath), not just independent angles
- Assess whether the physical damage pattern is consistent with the described incident
- For reconstruction_bullets: distil the reconstruction narrative into exactly 3 concise, specific bullet points
- For storyboard_panels: produce exactly 4 sequential panels covering the lifecycle of the incident.
  Each panel needs a single relevant emoji, a short title, and a 1-2 sentence plain-English description.
  Suggested titles: "Pre-Incident", "Point of Impact", "Immediate Aftermath", "Evidence Summary"
  (adjust titles to suit the claim — e.g. "Alleged Scenario" if the story is suspect)
- Return ONLY valid JSON with no markdown fences or extra text

Return a JSON object with exactly these fields:
{{
  "collision_type": "Front impact|Rear impact|Left-side impact|Right-side impact|Rollover|Multi-point|Parking/Low-speed|Underbody|Weather damage",
  "impact_direction": "Brief description of primary impact direction and angle",
  "reconstruction": "2-4 sentence narrative of what most likely happened based on damage evidence",
  "reconstruction_bullets": [
    "First key physical finding from the damage evidence",
    "Second key finding linking damage to the claimed scenario",
    "Third finding — overall story consistency verdict"
  ],
  "storyboard_panels": [
    {{"panel": 1, "title": "Pre-Incident", "emoji": "🚗", "description": "What was happening immediately before the incident — vehicle state, location, time, conditions."}},
    {{"panel": 2, "title": "Point of Impact", "emoji": "💥", "description": "The moment of collision — direction, speed estimate, what struck what."}},
    {{"panel": 3, "title": "Immediate Aftermath", "emoji": "🔧", "description": "Post-impact scene — which parts failed, airbag state, vehicle drivability."}},
    {{"panel": 4, "title": "Evidence Summary", "emoji": "🔍", "description": "What the physical evidence conclusively tells us — consistency verdict."}}
  ],
  "damage_matches_story": true or false,
  "confidence": <integer 0-100>,
  "similar_historical_cases": ["any matching case IDs from KB precedents, e.g. HIST-003"],
  "inconsistencies": ["specific inconsistencies found between damage and description, if any"],
  "status": "completed",
  "summary": "Most Probable: {{collision_type}} | Confidence: X% | Story Match: Yes/No"
}}
"""


def _telematics_summary(telematics: dict | None) -> str:
    if not telematics or not telematics.get("parsed_ok"):
        return "Not provided"
    parts = []
    if telematics.get("speed_kmph_at_event") is not None:
        parts.append(f"speed {telematics['speed_kmph_at_event']} km/h at event")
    if telematics.get("hard_braking_detected") is not None:
        parts.append(f"hard braking: {telematics['hard_braking_detected']}")
    if telematics.get("impact_g_force") is not None:
        parts.append(f"impact {telematics['impact_g_force']}g")
    if telematics.get("airbag_deployed") is not None:
        parts.append(f"airbag deployed: {telematics['airbag_deployed']}")
    return "; ".join(parts) if parts else "GPS trail only, no sensor readings"


class IncidentReconstructionAgent(BaseAgent):
    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        claim = context["claim"]
        damage = context["agents"].get("damage_assessment", {})
        damage_images = storage.get_claim_images(claim["claim_id"])

        # Dashcam frames (chronological stills extracted from uploaded video)
        # supplement or substitute for static damage photos.
        dashcam_frames = (context.get("docs") or {}).get("dashcam_frames") or []
        image_paths = damage_images + dashcam_frames
        image_note = (
            f"({len(damage_images)} damage photo(s) followed by {len(dashcam_frames)} "
            f"chronological dashcam frame(s))" if dashcam_frames else ""
        )

        damage_map = [
            {"part": p.get("part"), "severity": p.get("severity")}
            for p in damage.get("damaged_parts", [])
        ]
        damaged_part_names = [p.get("part", "") for p in damage.get("damaged_parts", [])]

        # RAG: fetch similar historical cases
        claim_type = claim.get("claim_type", "")
        kb_context = get_similar_cases_context(claim_type, damaged_part_names)

        telematics = (context.get("docs") or {}).get("telematics")

        prompt = BASE_PROMPT.format(
            kb_context=kb_context,
            description=claim.get("description", ""),
            vehicle=claim.get("vehicle", ""),
            claim_type=claim_type,
            damage_map=str(damage_map),
            telematics_summary=_telematics_summary(telematics),
            image_note=image_note,
        )
        result = ask_json(prompt, image_paths if image_paths else None,
                          agent_name="incident_reconstruction", claim_id=claim["claim_id"])
        result.setdefault("status", "completed")
        return result
