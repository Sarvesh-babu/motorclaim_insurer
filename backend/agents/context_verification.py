from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Any

import httpx

import storage
from agents.base_agent import BaseAgent
from config import OPENWEATHERMAP_API_KEY
from services.rag_client import get_policy_context

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OWM_URL = "https://api.openweathermap.org/data/2.5/weather"

# Per-process geocode cache. The Fraud agent (A2, GPS cross-check) and Context
# Verification (A4) both geocode the same incident location during one
# investigation; caching avoids the duplicate Nominatim call and keeps us under
# its ~1 req/s limit. Keyed by the normalized location string.
_GEOCODE_CACHE: dict[str, dict | None] = {}


def _geocode(location: str) -> dict | None:
    key = (location or "").strip().lower()
    if key in _GEOCODE_CACHE:
        # Return a copy so callers can never mutate the shared cached entry.
        cached = _GEOCODE_CACHE[key]
        return dict(cached) if cached else None
    result: dict | None = None
    try:
        r = httpx.get(
            NOMINATIM_URL,
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": "ClaimIntel/1.0"},
            timeout=5,
        )
        data = r.json()
        if data:
            result = {
                "lat": float(data[0]["lat"]),
                "lon": float(data[0]["lon"]),
                "display": data[0]["display_name"],
            }
    except Exception:
        result = None
    # Only cache successful lookups — a transient network failure shouldn't
    # poison the location as permanently ungeocodable for the rest of the run.
    if result is not None:
        _GEOCODE_CACHE[key] = result
    return dict(result) if result else None


def _get_weather(lat: float, lon: float) -> str:
    if not OPENWEATHERMAP_API_KEY:
        return "Weather data unavailable (no API key)"
    try:
        r = httpx.get(
            OWM_URL,
            params={"lat": lat, "lon": lon, "appid": OPENWEATHERMAP_API_KEY, "units": "metric"},
            timeout=5,
        )
        data = r.json()
        desc = data["weather"][0]["description"].title()
        temp = data["main"]["temp"]
        humidity = data["main"].get("humidity", "?")
        visibility = data.get("visibility", "?")
        return f"{desc}, {temp}°C, Humidity: {humidity}%, Visibility: {visibility}m"
    except Exception:
        return "Weather data unavailable"


def _check_policy_validity(claim: dict) -> str | None:
    """Returns a critical note if the incident falls outside the policy coverage period."""
    try:
        policy = storage.get_policy_by_phone(claim.get("phone", ""))
        if not policy:
            return None
        start_str = policy.get("policy_start", "")
        end_str = policy.get("policy_end", "")
        incident_str = claim.get("incident_date", "")
        if not (start_str and end_str and incident_str):
            return None
        start = datetime.strptime(start_str[:10], "%Y-%m-%d").date()
        end = datetime.strptime(end_str[:10], "%Y-%m-%d").date()
        incident = datetime.strptime(incident_str[:10], "%Y-%m-%d").date()
        if incident > end:
            days_lapsed = (incident - end).days
            return (
                f"POLICY EXPIRED: Incident ({incident_str[:10]}) is {days_lapsed} day(s) after "
                f"policy expiry ({end_str[:10]}). Claim NOT eligible — policy must be renewed."
            )
        if incident < start:
            days_before = (start - incident).days
            return (
                f"PRE-INCEPTION CLAIM: Incident ({incident_str[:10]}) is {days_before} day(s) before "
                f"policy start ({start_str[:10]}). Claim NOT eligible."
            )
        return None
    except Exception:
        return None


_GPS_MISMATCH_THRESHOLD_KM = 2.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0  # Earth radius, km
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _check_gps_trail(telematics: dict | None, geocoded: dict | None) -> dict:
    """Cross-check a telematics GPS trail against the geocoded claimed
    incident location. Soft signal — both data sources can be imprecise, so
    this only flags a mismatch beyond a generous threshold, not exact equality.
    """
    trail = (telematics or {}).get("gps_trail") or []
    if not trail:
        return {"gps_trail_available": False, "gps_location_match": None, "gps_distance_km": None}
    if not geocoded:
        return {"gps_trail_available": True, "gps_location_match": None, "gps_distance_km": None}

    # Nearest point in the trail to the claimed location — the trail may span
    # the approach to the incident, not just the impact point itself.
    nearest_km = min(
        _haversine_km(geocoded["lat"], geocoded["lon"], p["lat"], p["lon"])
        for p in trail if "lat" in p and "lon" in p
    )
    return {
        "gps_trail_available": True,
        "gps_location_match": nearest_km <= _GPS_MISMATCH_THRESHOLD_KM,
        "gps_distance_km": round(nearest_km, 2),
    }


def _assess_policy_coverage(policy_context: str, claim_type: str) -> str:
    """Simple keyword check against policy context to flag coverage issues."""
    if not policy_context:
        return "Policy document not available — manual coverage verification required"

    flags = []
    policy_lower = policy_context.lower()
    claim_lower = claim_type.lower() if claim_type else ""

    # Check for exclusion-triggering claim types
    if "flood" in claim_lower and "flood" in policy_lower and "exclusion" in policy_lower:
        flags.append("Flood coverage: verify if location is in excluded zone")
    if "theft" in claim_lower:
        flags.append("Theft claim: FIR mandatory; check duplicate key submission requirement")
    if "electrical" in claim_lower or "ev" in claim_lower:
        flags.append("EV/Electrical claim: HV safety protocol surcharge may apply")

    if flags:
        return "Coverage flags: " + "; ".join(flags)
    return "No obvious coverage exclusions detected from policy document"


class ContextVerificationAgent(BaseAgent):
    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        claim = context["claim"]
        location = claim.get("incident_location", "")
        policy_no = claim.get("policy_no", "")
        claim_type = claim.get("claim_type", "")

        # Hard eligibility check: is the incident within the policy coverage period?
        eligibility_note = _check_policy_validity(claim)

        # RAG: fetch policy document sections relevant to this claim
        policy_ctx = get_policy_context(policy_no, claim_type)
        coverage_note = (
            eligibility_note if eligibility_note
            else _assess_policy_coverage(policy_ctx, claim_type)
        )

        # Geocoding
        geo = _geocode(location)
        location_verified = geo is not None

        # Weather
        weather = "Unknown"
        if geo:
            weather = _get_weather(geo["lat"], geo["lon"])

        # Telematics GPS trail cross-check (optional evidence)
        telematics = (context.get("docs") or {}).get("telematics")
        gps_check = _check_gps_trail(telematics, geo)

        # Policy context summary (trimmed for output)
        policy_summary = ""
        if policy_ctx:
            # Extract first 300 chars as a summary
            raw = policy_ctx.replace("[KNOWLEDGE BASE", "").replace("[END KB CONTEXT]", "").strip()
            policy_summary = raw[:300] + ("..." if len(raw) > 300 else "")

        return {
            "status": "completed",
            "location_verified": location_verified,
            "geocoded_location": geo.get("display", location) if geo else location,
            "coordinates": {"lat": geo["lat"], "lon": geo["lon"]} if geo else None,
            "weather": weather,
            "gps_trail_available": gps_check["gps_trail_available"],
            "gps_location_match": gps_check["gps_location_match"],
            "gps_distance_km": gps_check["gps_distance_km"],
            "policy_coverage_note": coverage_note,
            "policy_document_excerpt": policy_summary or "Policy document not indexed — run scripts/build_vectorstore.py",
            "summary": (
                f"Weather: {weather} | "
                f"Location: {'Verified ✓' if location_verified else 'Unverified'} | "
                f"Policy: {coverage_note[:60]}..."
                if len(coverage_note) > 60 else
                f"Weather: {weather} | Location: {'Verified ✓' if location_verified else 'Unverified'} | Policy: {coverage_note}"
            ),
        }
