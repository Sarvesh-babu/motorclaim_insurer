# ClaimIntel — CLAUDE.md
> Codebase context for Claude Code. Read this first in every session.

---

## Project Overview

**ClaimIntel** is an agentic motor-insurance claim triage platform built for an ideathon.
It replaces manual claim investigation with a pipeline of 5 sequential AI agents powered by
**Groq-hosted LLaMA models** (llama-3.3-70b for text, llama-4-scout-17b for vision),
grounded by a local RAG knowledge base (ChromaDB + local ONNX embeddings).

- **Runs 100% on a laptop** — no cloud, no database, no managed deployment. *Storage, embeddings, and the React/FastAPI stack are entirely local — but claim text/images ARE transmitted to Groq's third-party API for inference, and incident location is sent to Nominatim/OpenWeatherMap. See "Data Residency & PII" below.*
- **Free APIs only** — Groq (6,000 text req/day · 1,000 vision req/day), Nominatim, OpenWeatherMap optional.
- **Stack:** FastAPI + Python 3.10+ · React 19 + Vite + Tailwind CSS 4 + Recharts · ChromaDB · fpdf2 + PyMuPDF + OpenCV.

### Data Residency & PII
- **Third-party transmission:** every agent call sends claim data to Groq (US-hosted). Context Verification additionally sends `incident_location` to Nominatim (EU) and OpenWeatherMap (US). No on-prem/local LLM option in this build.
- **PII masking before LLM calls:** `backend/services/pii.py` masks `phone` (last 4 digits) and `claimant` (first name + initial) via `build_llm_safe_claim()`, applied in `fraud_intelligence.py` and `settlement_recommendation.py` (the two agents that embed the full claim dict). Other agents only pull non-PII fields. Vehicle reg, policy_no, and location are sent unmasked — required for catalog/fraud/geocoding logic.
- **Encryption at rest:** `backend/services/crypto.py` (Fernet/AES via `ENCRYPTION_KEY` in `.env`) transparently encrypts the `claimant` and `phone` columns in `claims.csv` on write/read (`backend/storage.py`). Without a key the app runs but stores PII in plaintext. Uploaded binaries are not field-encrypted (a full-disk/volume concern).
- **Token/cost observability:** `backend/services/token_tracker.py` logs every Groq call to `data/token_usage.csv` (git-ignored), exposed via `GET /api/analytics/tokens` and the Analytics page.
- **Photo authenticity:** the damage agent flags AI-generated/stock/manipulated/screenshot images (`authenticity_flags`) plus a local EXIF-presence check (`_exif_authenticity_check`); either routes the claim through the image-quality gate into "Pending Review".

---

## System Architecture

```
Policyholder submits claim (phone lookup → auto-fill policy → photos + FIR/estimate/telematics/dash-cam)
         ↓
┌─────────────────────────────────────────────────────────────┐
│  KNOWLEDGE BASE (RAG — built once, queried per investigation)│
│  ├─ fraud_knowledge    15 indicators + 5 fraud schemes       │
│  ├─ vehicle_catalog    OEM/aftermarket pricing, 6 vehicles   │
│  ├─ claim_history      20 annotated historical cases         │
│  └─ policy_documents   customer policy PDFs                   │
│                 ChromaDB (local persistent)                  │
│         ONNX all-MiniLM-L6-v2 embeddings (384-dim, local)    │
└────────────────────┬────────────────────────────────────────┘
                     │ similarity search per agent
                     ▼
A1 Damage Assessment       — Groq Vision (llama-4-scout) + vehicle_catalog KB
A2 Fraud Intelligence      — 24 deterministic checks (FC-01…FC-24) + fraud_knowledge + claim_history KB
A3 Incident Reconstruction — Groq Vision (photos + dash-cam frames) + claim_history KB
A4 Context Verification    — Nominatim + telematics GPS + OpenWeatherMap + policy_documents KB
A5 Settlement Recommendation — all findings + all KB collections
         ↓
Deterministic guards + IRDAI settlement math (+ inflation cap) → SSE stream → React frontend
```

---

## Complete File Map

```
backend/
├── config.py                       ← loads .env; GROQ_API_KEY, thresholds, KB_DIR, VECTORSTORE_DIR
├── main.py                         ← FastAPI app, all routes, role guard (_require_adjudicator)
├── storage.py                      ← all CSV/JSON I/O, claim-ID gen, PII encryption hooks, _csv_lock
├── orchestrator.py                 ← runs A1→A5, parses docs/telematics/dash-cam, guards, SSE, result.json
├── agents/
│   ├── base_agent.py               ← abstract BaseAgent.run(context) -> dict
│   ├── damage_assessment.py        ← A1: Groq Vision + pricing engine + image-quality/authenticity gate
│   ├── fraud_intelligence.py       ← A2: _run_fraud_checks() = 24 checks (FC-01…FC-24) + fraud KB
│   ├── incident_reconstruction.py  ← A3: Groq Vision (photos + dash-cam frames) + claim_history
│   ├── context_verification.py     ← A4: geocode (memoized) + telematics GPS + weather + policy KB
│   └── settlement_recommendation.py← A5: all agents + all KB
└── services/
    ├── llm_client.py               ← Groq SDK wrapper (formerly gemini_client.py); ask_text/ask_json/ask_with_images
    ├── rag_client.py               ← ChromaDB query service (graceful fallback if not built)
    ├── pricing_engine.py           ← segment-aware parts pricing (+ optional live web prices)
    ├── settlement_calc.py          ← IRDAI breakdown + fair-value analysis + >40% inflation cap
    ├── garage_intel.py             ← cross-claim garage inflation scoring (FC-24 + analytics)
    ├── document_parser.py          ← FIR / garage-estimate PDF parsing (vision LLM)
    ├── telematics_parser.py        ← telematics/IoT JSON-or-CSV ingestion
    ├── video_parser.py             ← dash-cam video → evenly-spaced still frames (OpenCV)
    ├── report_generator.py         ← explainable PDF report (incl. settlement breakdown)
    ├── letter_generator.py         ← auto-drafted customer decision letter
    ├── pii.py                      ← build_llm_safe_claim() PII masking
    ├── crypto.py                   ← Fernet field encryption for claims.csv
    └── token_tracker.py            ← Groq token-usage logging → data/token_usage.csv

frontend/src/
├── App.jsx, utils/api.js (X-Role header), auth/ (login + ProtectedRoute), hooks/useInvestigationSSE.js
├── pages/        Login, ClaimsQueue, NewClaim, Dashboard, Analytics, NotFound
└── components/   InvestigationWorkflow (+ FraudCheckList), SettlementBreakdown, EvidencePanel,
                  AdjusterPanel, DecisionLetter, ClaimDecision, InvestigationSummary,
                  StoryboardPanel, VisualEvidenceAnalysis, ClaimOverview, Skeleton, ErrorBoundary

scripts/
├── generate_policy_pdfs.py         ← 3-page policy PDF per policy (fpdf2)
├── build_vectorstore.py            ← indexes KB → ChromaDB (LOCAL ONNX embeddings, no API)
├── generate_sample_telematics.py   ← sample telematics JSON (genuine / fraud / partial)
└── generate_demo_assets.py         ← the 3 live showcase cases → test/

data/
├── claims.csv                      ← claim records (ships EMPTY; created at runtime)
├── policies.csv                    ← 15 demo policies (phone → policy lookup)
├── claims/{claim_id}/              ← per-claim images, docs/, dashcam_frames/, result.json
├── kb/  fraud_indicators.json · vehicle_parts.json · claim_history.json · policies/*.pdf · sample_telematics/
├── token_usage.csv                 ← Groq usage log (git-ignored)
└── vectorstore/                    ← ChromaDB store (built by script; git-ignored)

test/                               ← showcase fixtures + per-case README (presenter adds photos/dash-cam)
```

---

## Backend — Key Decisions & Patterns

### Storage (no database)
- `claims.csv` fields: `claim_id, policy_no, claimant, phone, vehicle, claim_type, incident_date, incident_location, claim_amount, description, status, created_at, garage_estimate_amount, garage_workshop_name, fir_number`.
- `data/claims/{claim_id}/result.json` — all agent outputs + summary.
- `save_claim()` and `update_claim_field()` both hold `_csv_lock` (concurrent-write safe).
- `claim_amount` starts at 0 — set by Agent 1 (AI mid-estimate) after damage assessment.

### Customer flow
- Phone-first: `GET /api/customers/lookup?phone=XXX` auto-fills the policy. No manual policy/amount entry.

### Claim ID format
`CLM-{YYYY-MM}-{count:06d}` — `count` is max(existing CSV IDs ∪ on-disk folders)+1 (never reuses a deleted ID).

### LLM Client (`backend/services/llm_client.py`)
> **Uses the Groq SDK, not Gemini.** Formerly `gemini_client.py`; renamed once the last Gemini reference was gone. All agents import `from services.llm_client import …`.
```python
TEXT_MODEL   = "llama-3.3-70b-versatile"                    # A2, A4, A5 (text)
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"  # A1, A3 (image+text)
```
- `ask_text` / `ask_with_images` / `ask_json` (4-pass JSON repair). Multi-image calls interleave `[Image N]` labels so the vision model can set `bounding_box.image_index` correctly. 2 retries, 5s delay.

### Orchestrator (`backend/orchestrator.py`)
- Parses uploaded docs (FIR/estimate), telematics, and dash-cam frames into `context["docs"]` **before** the agent loop, so A2's fraud checks can use them.
- Runs A1→A5 async; emits SSE `agent_start/agent_done/agent_error/done/ping`; writes `result.json` incrementally.
- After A1: writes the AI mid-estimate to `claim_amount`.
- Hard guards (override the LLM): policy expiry → Reject; damage-vs-story mismatch → block auto-approve → Escalate; low confidence / failed image gate → Pending Review.
- Computes the settlement breakdown and reconciles the headline `recommended_settlement` to `net_payable` on Approve.

### RAG Client (`backend/services/rag_client.py`)
- Graceful fallback: no vectorstore → returns `""` → agents work without KB context.
- Embeddings fully local (ChromaDB built-in ONNX `all-MiniLM-L6-v2`, 384-dim) — no API, no key.

---

## Agent 2 — Fraud Intelligence: 24 deterministic checks (FC-01…FC-24)

`_run_fraud_checks(claim, damage, docs)` returns a list of `{id, name, status, detail}` where status ∈ `pass | flag | na`. Only `flag` results feed the LLM prompt and the score; all 24 are surfaced in the UI **Fraud Check List** panel (toggle on the fraud agent row). Counts exposed as `fraud_check_counts`.

- FC-01 high-value threshold · FC-02 prior claims · FC-03 policy validity · FC-04 new-policy syndrome (tiered) · FC-05 reporting delay · FC-06 night-time · FC-07 description red-flags · FC-08 repeat-location · FC-09 30-day clustering · FC-10 duplicate description · FC-11 claim-to-SI ratio · FC-12 damage-present · FC-13 vehicle identity · FC-14 pre-existing damage · FC-15 plate verification · FC-16 multiple vehicles · FC-17 workshop inflation (this claim) · FC-18 FIR present (theft/TP) · FC-19 estimate-without-photos · FC-20 FIR consistency cross-check · FC-21 telematics impact · FC-22 telematics GPS · FC-23 garage under-declaration · **FC-24 cross-claim garage inflation (FS-004)**.
- **Score is Python-authoritative:** deterministic base score from flag weights (CRITICAL 35, NPS tiered, else 8), LLM clamped to base ±15. Label derived from final score (`<40 Low / 40–69 Medium / 70+ High`). The `summary` string is **regenerated** from the authoritative score/label (never the stale LLM value).
- **FC-24** uses `services/garage_intel.py` to scan prior investigated claims for the same `garage_workshop_name` and flag systematic inflation.

---

## Settlement (`backend/services/settlement_calc.py`)

Deterministic, IRDAI-aligned, no LLM:
```
Net Payable = Approved Repair Basis − Depreciation (material-wise) − Deductibles (− Salvage on total loss)
```
- **Fair-value bands** (garage quote vs AI assessed fair value): ±20% Consistent · +20–40% Review · **>40% Investigate**.
- **Inflation cap:** only the **>40% (Investigate)** band changes the payout — repair basis becomes the **AI fair value**, the excess is `disallowed_inflation` (surfaced as *leakage prevented* in analytics), and `inflation_cap_applied=True`. Consistent/Review use the garage quote.
- Material-wise depreciation (glass 0%, tyre/rubber/battery 50%, fibre 30%, metal by age slab). GST treated as already included. Total-loss path: IDV − salvage when repair ≥ 75% of sum insured. NCB advisory on Approve.
- `SettlementBreakdown.jsx` first row reads "AI Assessed Value (Inflation Cap Applied)" when capped; the PDF report mirrors the breakdown.

---

## Frontend — Key Patterns

- **Routing:** `/login`, `/` ClaimsQueue, `/new` NewClaim, `/claims/:id` Dashboard, `/analytics`, `*` NotFound. Investigate/decision are role-guarded (`X-Role: adjudicator`, set from `localStorage` session in `utils/api.js`).
- **SSE hook (`useInvestigationSSE`):** POST investigate → EventSource → merges `liveAgents`; on `done` calls `onDone` (Dashboard `fetchResult`, which re-fetches both `/claims/{id}` **and** `/docs` so parsed FIR/telematics + dash-cam frames appear immediately). Clears `esRef` on done/error.
- **Vite proxy:** `/api` and `/uploads` → backend.
- **Bounding boxes:** `VisualEvidenceAnalysis.jsx` draws percentage-coord boxes per `image_index`; Severe=red/Moderate=amber/Minor=green.

---

## Knowledge Base (data/kb/)
- `fraud_indicators.json` — 15 indicators (FI-POL/CLM/DMG/LOC/INF/BEH) + 5 schemes (FS-001 New-Policy Pre-Existing · FS-002 Staged Collision · FS-003 Serial Theft · FS-004 Workshop Inflation · FS-005 Weather Fabrication) + IRDAI depreciation/scoring.
- `vehicle_parts.json` — OEM/aftermarket/labour for 6 vehicles (Swift Dzire, Alto K10, City ZX, Amaze, Innova Crysta, Creta SX, Nexon EV).
- `claim_history.json` — 20 annotated historical cases (HIST-001…020).
- `policies/` — policy PDFs (fpdf2; Latin-1 sanitized via `_s()`; parsed by PyMuPDF at build).
- `sample_telematics/` — genuine / fraud-mismatch / partial-data JSON for the upload demo.

---

## API Routes
```
GET  /api/health
GET  /api/kb/status
GET  /api/customers/lookup?phone=XXX
POST /api/claims/                              create claim
GET  /api/claims/                              list (enriched with decision)
GET  /api/claims/{id}                          {claim, result, policy} (+ lazy breakdown backfill)
GET  /api/claims/{id}/images   ·  DELETE /images/{filename}   ·  POST /files (doc_type: images|estimate|fir|dashcam|telematics)
GET  /api/claims/{id}/docs                     {parsed, files, dashcam_frames}
POST /api/claims/{id}/investigate              [adjudicator] background pipeline
GET  /api/claims/{id}/stream                   SSE events
GET  /api/claims/{id}/report                   PDF report (incl. settlement breakdown)
POST /api/claims/{id}/adjuster/decision        [adjudicator]   ·  POST /adjuster/notes [adjudicator]
GET  /api/claims/{id}/letter                   customer decision letter (LLM)
GET  /api/analytics            ·  GET /api/analytics/tokens     (+ garage_intel table)
GET  /api/sample-telematics/{filename}
GET  /uploads/{claim_id}/...                   StaticFiles
```

---

## Demo customers & the 3 showcase cases

`data/policies.csv` has 15 policies. The live demo uses three (assets generated by `python scripts/generate_demo_assets.py` → `test/`, filed live as **adjudicator**, garage-amount field left blank):

| Case | Customer (phone) | Generated evidence | Presenter adds | Expected |
|---|---|---|---|---|
| A genuine | Rajesh Kumar (`9876543210`) | consistent estimate + genuine telematics | damage photos | **Approve** |
| B inflation+FIR | Meera Iyer (`8890123456`, POL-445566) | inflated estimate + mismatched FIR | front-damage photos | **Escalate** (FC-17/FS-004 + FC-20 + inflation cap) |
| C staged collision | Arjun Mehta (`9900112233`) | consistent estimate + fraud telematics | dash-cam .mp4 | **Escalate** (FC-21 + FC-22 + FC-02) |

`claims.csv` ships **empty** — there are no pre-seeded result.json claims; everything is filed live. `test/README.md` has the exact per-case filing steps.

---

## Known gotchas / conventions
- **fpdf2 is Latin-1 only** — sanitize all text via a `_s()` helper (₹→"Rs.", em-dash→"-", etc.); don't mix `cell()` + `multi_cell()` on one row (fpdf2 silently drops the value).
- **Embeddings are local ONNX** — `build_vectorstore.py` needs no API key; agents fall back gracefully if the store isn't built.
- **Investigate as adjudicator** — `/investigate` 403s otherwise (by design).
- **Dash-cam** must be a standard `.mp4` (H.264) for OpenCV; unreadable video → 0 frames (graceful, not an error).
- **GPS / weather** need network at investigate time; offline → those checks report N/A, never crash.

---

## Current state
- Backend (FastAPI, all routes), 5 agents, frontend (all pages incl. Analytics), PII masking + at-rest encryption, token observability, 24 fraud checks, garage intelligence, settlement breakdown + inflation cap, telematics + dash-cam evidence, PDF report + customer letter — all working; frontend production build verified.
- ChromaDB vectorstore is **built locally** via `scripts/build_vectorstore.py` (not committed; rebuilt on demand).
- `claims.csv` ships empty; demo claims are filed live from `test/` fixtures.
