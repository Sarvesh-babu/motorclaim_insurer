"""Lightweight LLM token usage observability — logs every Groq call to a CSV
and aggregates totals so the team can see consumption against the free-tier
daily caps (6,000 text req/day / 1,000 vision req/day) before they're hit.
"""

import csv
import os
import threading
from datetime import datetime, date

import storage
from config import DATA_DIR

TOKEN_USAGE_CSV = os.path.join(DATA_DIR, "token_usage.csv")

FIELDS = ["timestamp", "claim_id", "agent", "model",
          "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"]

_lock = threading.Lock()

# Groq free-tier daily request caps (not token caps) — used only as a rough
# "approaching limit" signal based on call counts logged here.
DAILY_TEXT_REQUEST_CAP = 6000
DAILY_VISION_REQUEST_CAP = 1000
VISION_MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"

# Groq list pricing, USD per 1M tokens (input/output) — approximate, update if
# Groq's published rates change. Falls back to the text-model rate for any
# model not listed here.
MODEL_PRICING_PER_MILLION = {
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    VISION_MODEL_NAME: {"input": 0.11, "output": 0.34},
}
_DEFAULT_PRICING = {"input": 0.59, "output": 0.79}


def _calc_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = MODEL_PRICING_PER_MILLION.get(model, _DEFAULT_PRICING)
    cost = (prompt_tokens / 1_000_000) * rates["input"] + (completion_tokens / 1_000_000) * rates["output"]
    return round(cost, 6)


def _ensure_csv():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(TOKEN_USAGE_CSV):
        with open(TOKEN_USAGE_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()


def log_usage(claim_id: str | None, agent_name: str | None, model: str,
              prompt_tokens: int, completion_tokens: int, total_tokens: int) -> None:
    _ensure_csv()
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "claim_id": claim_id or "",
        "agent": agent_name or "",
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": _calc_cost_usd(model, prompt_tokens, completion_tokens),
    }
    with _lock:
        with open(TOKEN_USAGE_CSV, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow(row)


def _read_rows() -> list[dict]:
    if not os.path.exists(TOKEN_USAGE_CSV):
        return []
    with open(TOKEN_USAGE_CSV, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_usage_summary(claim_id: str | None = None) -> dict:
    rows = _read_rows()

    # Only surface usage for claims that still exist in claims.csv — rows left
    # over from deleted/reset demo claims would otherwise inflate the
    # Analytics token dashboard with stale historic data.
    live_claim_ids = {c["claim_id"] for c in storage.get_all_claims()}
    rows = [r for r in rows if r.get("claim_id") in live_claim_ids]

    if claim_id:
        rows = [r for r in rows if r.get("claim_id") == claim_id]

    today_str = date.today().isoformat()
    today_rows = [r for r in rows if r.get("timestamp", "").startswith(today_str)]

    def _sum(rs, field):
        total = 0
        for r in rs:
            try:
                total += int(r.get(field) or 0)
            except ValueError:
                pass
        return total

    def _sum_cost(rs):
        total = 0.0
        for r in rs:
            try:
                total += float(r.get("cost_usd") or 0)
            except ValueError:
                pass
        return round(total, 6)

    by_agent: dict[str, dict] = {}
    for r in rows:
        agent = r.get("agent") or "unknown"
        slot = by_agent.setdefault(agent, {"calls": 0, "total_tokens": 0, "cost_usd": 0.0})
        slot["calls"] += 1
        slot["total_tokens"] += int(r.get("total_tokens") or 0)
        try:
            slot["cost_usd"] = round(slot["cost_usd"] + float(r.get("cost_usd") or 0), 6)
        except ValueError:
            pass

    today_text_calls = sum(1 for r in today_rows if r.get("model") != VISION_MODEL_NAME)
    today_vision_calls = sum(1 for r in today_rows if r.get("model") == VISION_MODEL_NAME)

    return {
        "total_calls": len(rows),
        "total_prompt_tokens": _sum(rows, "prompt_tokens"),
        "total_completion_tokens": _sum(rows, "completion_tokens"),
        "total_tokens": _sum(rows, "total_tokens"),
        "total_cost_usd": _sum_cost(rows),
        "by_agent": by_agent,
        "today": {
            "date": today_str,
            "text_calls": today_text_calls,
            "vision_calls": today_vision_calls,
            "total_tokens": _sum(today_rows, "total_tokens"),
            "text_calls_cap": DAILY_TEXT_REQUEST_CAP,
            "vision_calls_cap": DAILY_VISION_REQUEST_CAP,
            "approaching_text_limit": today_text_calls >= DAILY_TEXT_REQUEST_CAP * 0.8,
            "approaching_vision_limit": today_vision_calls >= DAILY_VISION_REQUEST_CAP * 0.8,
        },
    }
