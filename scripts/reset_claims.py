"""Reset the claims queue and dashboard data back to a fresh-install state.

Wipes:
  - data/claims.csv          -> header only
  - data/token_usage.csv     -> header only
  - data/claims/{claim_id}/  -> all per-claim folders deleted

Leaves untouched: data/policies.csv, data/kb/ (knowledge base), data/vectorstore/.

Usage:
    python scripts/reset_claims.py          # asks for confirmation
    python scripts/reset_claims.py --yes    # skips confirmation

After running, restart the backend container so it doesn't serve stale
in-memory state from before the reset:
    docker compose restart backend
"""

import csv
import io
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")

CLAIMS_CSV = os.path.join(DATA_DIR, "claims.csv")
TOKEN_USAGE_CSV = os.path.join(DATA_DIR, "token_usage.csv")
CLAIMS_DIR = os.path.join(DATA_DIR, "claims")

# Mirrors backend/storage.py CSV_FIELDS and backend/services/token_tracker.py FIELDS.
# Keep in sync if those change.
CLAIMS_FIELDS = [
    "claim_id", "policy_no", "claimant", "phone", "vehicle",
    "claim_type", "incident_date", "incident_location", "claim_amount",
    "description", "status", "created_at",
    "garage_estimate_amount", "garage_workshop_name", "fir_number",
]
TOKEN_USAGE_FIELDS = [
    "timestamp", "claim_id", "agent", "model",
    "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd",
]


def _write_header_only(path: str, fields: list[str]) -> None:
    buf = io.StringIO()
    csv.writer(buf).writerow(fields)
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(buf.getvalue())


def main() -> None:
    if "--yes" not in sys.argv:
        claim_count = max(0, sum(1 for _ in open(CLAIMS_CSV, encoding="utf-8")) - 1) if os.path.exists(CLAIMS_CSV) else 0
        folder_count = len(os.listdir(CLAIMS_DIR)) if os.path.isdir(CLAIMS_DIR) else 0
        answer = input(
            f"This will permanently delete {claim_count} claim row(s), {folder_count} claim folder(s), "
            f"and all token usage history. Continue? [y/N] "
        )
        if answer.strip().lower() != "y":
            print("Aborted.")
            return

    _write_header_only(CLAIMS_CSV, CLAIMS_FIELDS)
    _write_header_only(TOKEN_USAGE_CSV, TOKEN_USAGE_FIELDS)

    if os.path.isdir(CLAIMS_DIR):
        for name in os.listdir(CLAIMS_DIR):
            path = os.path.join(CLAIMS_DIR, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

    print("Done - claims.csv, token_usage.csv and data/claims/ are reset.")
    print("Restart the backend so it doesn't serve stale state: docker compose restart backend")


if __name__ == "__main__":
    main()
