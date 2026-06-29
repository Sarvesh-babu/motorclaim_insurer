# ClaimIntel — AGENTS.md
> Codebase context for Codex. Read this first in every session.

---

## Project Overview

**ClaimIntel** is an agentic motor insurance claim triage platform built for an ideathon.
It replaces manual claim investigation with a pipeline of 5 sequential AI agents powered by
**Groq-hosted LLaMA models** (llama-3.3-70b for text, llama-4-scout-17b for vision),
grounded by a local RAG knowledge base (ChromaDB + local ONNX embeddings).

- **Runs 100% on a laptop** — no cloud, no database, no deployment
- **Free APIs only** — Groq (6,000 text req/day · 1,000 vision req/day), Nominatim, OpenWeatherMap optional
- **Stack:** FastAPI + Python 3.10 · React 18 + Vite + Tailwind CSS · ChromaDB · fpdf2 + PyMuPDF

---

## System Architecture (Current — with RAG)

```
Customer submits claim (phone lookup → auto-fill policy)
         ↓
┌─────────────────────────────────────────────────────────────┐
│  KNOWLEDGE BASE (RAG — built once, queried per investigation)│
│  ├─ fraud_knowledge    15 indicators + 5 fraud schemes       │
│  ├─ vehicle_catalog    OEM/aftermarket pricing, 6 vehicles   │
│  ├─ claim_history      20 annotated historical cases         │
│  └─ policy_documents   5 customer policy PDFs (3 pages each)│
│                 ChromaDB (local persistent)                  │
│         ONNX all-MiniLM-L6-v2 embeddings (384-dim, local)   │
└────────────────────┬────────────────────────────────────────┘
                     │ similarity search per agent
                     ▼
Agent 1: Damage Assessment     — Groq Vision (llama-4-scout) + vehicle_catalog KB
Agent 2: Fraud Intelligence    — Rules + fraud_knowledge + claim_history KB
Agent 3: Incident Reconstruction — Groq Vision (llama-4-scout) + claim_history KB
Agent 4: Context Verification  — Nominatim + OpenWeatherMap + policy_documents KB
Agent 5: Settlement Recommendation — All findings + all KB collections
         ↓
Live SSE stream → React frontend (real-time agent status)
```

---

## Complete File Map

```
Ideathon_Motor_Claim/
├── AGENTS.md                          ← this file
├── PLAN.md                            ← current sprint plan
├── BLUEPRINT.md                       ← original design doc
├── README.md                          ← 5-step setup guide
├── requirements.txt                   ← Python deps (incl. chromadb, fpdf2, PyMuPDF)
├── .env                               ← GROQ_API_KEY (never commit)
├── .env.example
│
├── backend/
│   ├── config.py                      ← loads .env; exposes paths incl. KB_DIR, VECTORSTORE_DIR
│   ├── main.py                        ← FastAPI app, all routes
│   ├── storage.py                     ← all CSV/JSON file I/O
│   ├── orchestrator.py                ← runs A1→A5, SSE events, writes result.json
│   ├── agents/
│   │   ├── base_agent.py              ← abstract BaseAgent with run(context) -> dict
│   │   ├── damage_assessment.py       ← Agent 1: Groq Vision + vehicle_catalog RAG
│   │   ├── fraud_intelligence.py      ← Agent 2: rules + fraud_knowledge RAG
│   │   ├── incident_reconstruction.py ← Agent 3: Groq Vision + claim_history RAG
│   │   ├── context_verification.py    ← Agent 4: geo + weather + policy_documents RAG
│   │   └── settlement_recommendation.py ← Agent 5: all agents + all KB RAG
│   └── services/
│       ├── llm_client.py              ← Groq SDK wrapper (formerly gemini_client.py); ask_text, ask_json, ask_with_images
│       ├── rag_client.py             ← ChromaDB query service (graceful fallback if not built)
│       ├── settlement_calc.py        ← deterministic IRDAI settlement breakdown (#2)
│       └── letter_generator.py       ← auto-drafted customer decision letter (#7)
│
├── frontend/
│   ├── vite.config.js                 ← Tailwind plugin + proxy: /api→:8000, /uploads→:8000
│   └── src/
│       ├── App.jsx                    ← BrowserRouter, Nav, ErrorBoundary, routes
│       ├── utils/api.js               ← Axios instance, baseURL: '/api'
│       ├── hooks/useInvestigationSSE.js ← SSE hook: POST investigate → EventSource → state
│       ├── pages/
│       │   ├── ClaimsQueue.jsx        ← claims table with skeleton, error banner
│       │   ├── NewClaim.jsx           ← phone lookup → PolicyCard → incident form
│       │   ├── Dashboard.jsx          ← 3-col + 2-col grid, SSE live updates
│       │   └── NotFound.jsx           ← 404 page
│       └── components/
│           ├── ClaimOverview.jsx      ← claim header card
│           ├── InvestigationWorkflow.jsx ← 5-agent step pipeline with live dots
│           ├── InvestigationSummary.jsx  ← Recharts donut + 4 check rows
│           ├── ClaimDecision.jsx      ← Approve/Reject/Escalate badge + settlement
│           ├── SettlementBreakdown.jsx ← collapsible IRDAI itemised breakdown (#2)
│           ├── AdjusterPanel.jsx      ← human decision buttons + notes thread (#1)
│           ├── DecisionLetter.jsx     ← draft/edit/copy customer letter (#7)
│           ├── EvidencePanel.jsx      ← 3 tabs: Evidence / Reasoning Trail / Timeline
│           ├── VisualEvidenceAnalysis.jsx ← canvas bounding box overlay on images
│           ├── Skeleton.jsx           ← SkeletonBar, SkeletonCard, SkeletonDashboard
│           └── ErrorBoundary.jsx      ← React class component, catches render errors
│
├── scripts/
│   ├── generate_policy_pdfs.py        ← argparse script; generates 3-page PDF per policy (fpdf2)
│   └── build_vectorstore.py          ← indexes all KB → ChromaDB (Gemini embeddings)
│
└── data/
    ├── claims.csv                     ← master claim list (CSV, no DB)
    ├── policies.csv                   ← 5 customer policies with phone numbers
    ├── claims/
    │   ├── CLM-2025-05-000001/        ← Rajesh Kumar (genuine, Approve, 14% fraud)
    │   ├── CLM-2025-05-000002/        ← Mohammed Faiz (fraud, Escalate, 76% fraud)
    │   └── CLM-2025-05-000003/        ← Priya Sharma (mixed, Escalate, 38% fraud)
    ├── kb/
    │   ├── fraud_indicators.json      ← 15 indicators + 5 schemes + IRDAI thresholds
    │   ├── vehicle_parts.json         ← OEM/aftermarket parts pricing (Indian market)
    │   ├── claim_history.json         ← 20 historical claims with decisions + lessons
    │   └── policies/                  ← POL-784512.pdf ... POL-567890.pdf (5 PDFs, ✅ generated)
    └── vectorstore/                   ← ChromaDB persistent store (⬜ not yet built)
```

---

## Backend — Key Decisions & Patterns

### Storage (no database)
- `claims.csv` — all claim metadata. Fields: `claim_id, policy_no, claimant, phone, vehicle, claim_type, incident_date, incident_location, claim_amount, description, status, created_at`
- `data/claims/{claim_id}/result.json` — all agent outputs + summary per claim
- `update_claim_field(claim_id, field, value)` — reads all rows, modifies, rewrites entire CSV (no append-only limitation)
- `claim_amount` starts at 0 — set by Agent 1 after damage assessment via `update_claim_field`

### Customer Flow (major UX redesign from original blueprint)
- **Original:** Customer types policy number + claim amount manually
- **Implemented:** Customer enters phone number → `GET /api/customers/lookup?phone=XXX` → auto-fills policy. Claim amount = 0 until Agent 1 sets it. No manual entry of either field.

### Claim ID format
`CLM-{YYYY-MM}-{count:06d}` e.g. `CLM-2025-05-000001`

### LLM Client (`backend/services/llm_client.py`)
> **Uses the Groq SDK, not Gemini.** (Formerly `gemini_client.py` — renamed to `llm_client.py`
> once the last Gemini reference was gone; all agents import `from services.llm_client import ask_json`.)

Uses the **Groq SDK** with two models:
```python
from groq import Groq
TEXT_MODEL   = "llama-3.3-70b-versatile"                   # Agents 2, 4, 5 (text-only)
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct" # Agents 1, 3 (image+text)
```
Three public functions (same API as original Gemini wrapper):
- `ask_text(prompt)` → str
- `ask_with_images(prompt, image_paths)` → str
- `ask_json(prompt, image_paths=None)` → dict — 4-pass JSON repair (clean → extract → repair → re-ask)

**Rate limits (Groq free tier):** 6,000 text req/day · 1,000 vision req/day · 30 req/min
**Retry:** 2 retries with 5s delay on transient errors.

### Orchestrator (`backend/orchestrator.py`)
- Runs A1→A5 sequentially (async)
- After A1 completes: calls `update_claim_field(claim_id, "claim_amount", amount)`
- Emits SSE events: `agent_start`, `agent_done`, `agent_error`, `done`, `ping`
- Writes each agent result to `result.json` after completion (incremental — no wait for all 5)

### RAG Client (`backend/services/rag_client.py`)
- **Graceful fallback**: if `data/vectorstore/` doesn't exist or collection missing → returns `""` → agents work without KB context
- **Embeddings are fully local**: ChromaDB's built-in ONNX `all-MiniLM-L6-v2` (384-dim) — no API calls, no rate limits
- Five public helpers for agents: `get_vehicle_pricing_context()`, `get_fraud_kb_context()`, `get_similar_cases_context()`, `get_policy_context()`, `get_settlement_context()`

---

## Frontend — Key Decisions & Patterns

### Routing
```
/           → ClaimsQueue
/new        → NewClaim
/claims/:id → Dashboard
*           → NotFound
```

### NewClaim flow
1. Enter phone number → click "Find Policy"
2. `GET /api/customers/lookup?phone=XXX` → PolicyCard auto-fills (name, vehicle, policy_no, coverage)
3. Fill incident details (type, date, location, description, photos)
4. Submit → `POST /api/claims/` (form fields: policy_no, claimant, phone, vehicle, claim_type, incident_date, incident_location, description)

### SSE Hook (`useInvestigationSSE`)
- Stores EventSource ref to avoid duplicate connections
- On `agent_done` event: updates `liveAgents` state (merges with existing result)
- On `done` event: calls `onDone()` which re-fetches full result from API
- Exposes: `{ agentStatuses, liveAgents, investigating, start }`

### Vite Proxy
```js
// vite.config.js — both paths proxied
'/api'     → http://localhost:8000
'/uploads' → http://localhost:8000
```

### Bounding Box Canvas
`VisualEvidenceAnalysis.jsx` — draws boxes from percentage coordinates (x%, y%, w%, h%) onto actual pixel dimensions using `canvas.getBoundingClientRect()`. Colors: Severe=red, Moderate=amber, Minor=green.

---

## The Five Agents (Updated with RAG)

| Agent | Input | KB Collection | Key Output Fields |
|---|---|---|---|
| **A1 Damage Assessment** | Images + description + vehicle | `vehicle_catalog` (parts pricing) | `damaged_parts[]`, `total_repair_estimate`, `overall_severity`, `consistent_with_description` |
| **A2 Fraud Intelligence** | Claim + A1 result + rule flags | `fraud_knowledge` (indicators, schemes) | `fraud_score` (0–100), `fraud_label`, `indicators[]`, `matched_schemes[]`, `kb_references[]` |
| **A3 Incident Reconstruction** | Images + description + damage map | `claim_history` (similar cases) | `collision_type`, `reconstruction`, `damage_matches_story`, `confidence`, `similar_historical_cases[]` |
| **A4 Context Verification** | Location + policy_no + claim_type | `policy_documents` (coverage terms) | `location_verified`, `geocoded_location`, `weather`, `policy_coverage_note`, `policy_document_excerpt` |
| **A5 Settlement Recommendation** | All A1–A4 + fraud_score + damage_severity | `claim_history` + `fraud_knowledge` | `decision` (Approve/Reject/Escalate), `recommended_settlement`, `reasoning_trail[]`, `kb_precedents_applied[]` |

### Rule-based flags in Agent 2 (before LLM call)
- Claim amount > ₹2,00,000 → mandatory surveyor flag
- 2+ prior claims on same policy → serial claimant pattern flag
- Policy age < 60 days → new policy syndrome flag (cross-checks `policies.csv`)
- Claim filed > 7 days after incident → late reporting flag

---

## Knowledge Base (data/kb/)

### fraud_indicators.json
15 fraud indicators across 5 categories:
- Policy Red Flags (FI-POL-001, -002, -003)
- Claim Pattern Fraud (FI-CLM-001, -002, -003)
- Damage Inconsistency (FI-DMG-001, -002, -003, -004)
- Location & Circumstance (FI-LOC-001, -002)
- Inflation/Workshop Fraud (FI-INF-001, -002)
- Behavioural Indicators (FI-BEH-001, -002)

5 known fraud schemes:
- FS-001: New Policy Pre-Existing Damage
- FS-002: Staged Collision
- FS-003: Serial Theft / Total Loss
- FS-004: Workshop Inflation Conspiracy
- FS-005: Weather Claim Fabrication

IRDAI depreciation schedule + scoring thresholds also included.

### vehicle_parts.json
OEM + aftermarket pricing + labour for 6 vehicles:
- Maruti Suzuki Swift Dzire, Alto K10
- Honda City ZX, Amaze
- Toyota Innova Crysta
- Hyundai Creta SX
- Tata Nexon EV (EV-specific notes, battery pack pricing)

Includes common repair packages, total loss thresholds, standard labour rates by city tier.

### claim_history.json
20 fully annotated historical cases (HIST-001 to HIST-020):
- Each has: vehicle, claim_type, damaged_parts, fraud_score, fraud_indicators, decision, settlement, lesson
- Covers: genuine collisions, staged accidents, flood claims (genuine + fraudulent), theft, hail, animal strike, workshop inflation, serial claimants

### policies/ (PDF documents)
5 PDFs generated by `scripts/generate_policy_pdfs.py` using fpdf2.
- 3 pages per policy: Schedule, Coverage Details, Exclusions & Claims Procedure
- Encoding: Latin-1 (Helvetica); all Unicode chars sanitized via `_s()` helper
- Text extracted by PyMuPDF (fitz) during vectorstore build

---

## Demo Customers & Phone Numbers

| Phone | Name | Vehicle | Policy | Notes |
|---|---|---|---|---|
| `9876543210` | Rajesh Kumar | Maruti Swift Dzire | POL-784512 | Old policy, 0 prior claims, 20% NCB |
| `9845012345` | Priya Sharma | Honda City ZX | POL-234567 | Normal policy |
| `9988776655` | Mohammed Faiz | Toyota Innova Crysta | POL-891234 | ⚠️ Policy issued Apr 2025 (new!) — fraud demo |
| `9123456789` | Kavitha Reddy | Tata Nexon EV | POL-345678 | EV policy, Battery Guard add-on |
| `9900112233` | Arjun Mehta | Hyundai Creta SX | POL-567890 | 1 prior claim, 25% NCB |

---

## Pre-seeded Demo Claims

| Claim ID | Customer | Expected Decision | Fraud Score |
|---|---|---|---|
| CLM-2025-05-000001 | Rajesh Kumar | **Approve** | 14% Low |
| CLM-2025-05-000002 | Mohammed Faiz | **Escalate** | 76% High |
| CLM-2025-05-000003 | Priya Sharma | **Escalate** | 38% Medium |

---

## API Routes

```
GET  /api/health                              → {"status":"ok"}
GET  /api/kb/status                           → {built, collections[]}
GET  /api/customers/lookup?phone=XXX         → policy dict or 404
POST /api/claims/                             → create claim, returns claim_id
GET  /api/claims/                             → list all claims (enriched with decision)
GET  /api/claims/{id}                         → {claim, result, policy}
GET  /api/claims/{id}/images                  → {images: ["/uploads/..."]}
DELETE /api/claims/{id}/images/{filename}     → delete a single image
POST /api/claims/{id}/files                   → multipart image upload
GET  /api/claims/{id}/docs                    → {parsed, files} for estimate/FIR
POST /api/claims/{id}/investigate             → trigger agent pipeline (background)
GET  /api/claims/{id}/stream                  → SSE stream of agent events
GET  /api/claims/{id}/report                  → download full PDF investigation report
POST /api/claims/{id}/adjuster/decision       → record human decision {decision, adjuster, reason}
POST /api/claims/{id}/adjuster/notes          → add adjuster note {author, text}
GET  /api/claims/{id}/letter                  → draft customer decision letter (LLM)
GET  /api/analytics                           → aggregate stats for all claims
GET  /uploads/{claim_id}/{filename}           → serve uploaded images/docs (StaticFiles)
```

---

## How to Run

```bash
# 1. Set API key
cp .env.example .env
# Edit .env — fill in GROQ_API_KEY (free at https://console.groq.com)

# 2. Start everything
docker compose up --build
# → http://localhost:3000

# 3. Build the knowledge base (one-time — local ONNX, no API calls, ~30s)
docker compose exec backend python /app/scripts/build_vectorstore.py
```

**Notes:**
- `./data/` is bind-mounted — claims, uploads, vectorstore and results all persist on your host across restarts/rebuilds
- The ChromaDB ONNX model (~79 MB) is baked into the image at build time, so investigations start immediately
- Policy PDFs are pre-generated and included in `data/kb/policies/`; only re-run `generate_policy_pdfs.py` if you add new policies
- Backend is also reachable directly at `http://localhost:8001` for curl / Postman

**Useful commands:**
```bash
docker compose up --build        # rebuild images and start
docker compose up                # start without rebuild
docker compose down              # stop
docker compose logs -f backend   # tail backend logs
docker compose exec backend python /app/scripts/build_vectorstore.py  # build KB
```

---

## Build History — What Was Done (Chronological)

### Phase 1 — Foundation
- Created full project folder structure
- Wrote `backend/config.py`, `storage.py`, `main.py` (FastAPI with all routes)
- CSV storage with `get_all_claims()`, `save_claim()`, `update_claim_field()`, `get_policy_by_phone()`
- LLM client originally Gemini → **migrated to Groq SDK** (file kept as `gemini_client.py` for import compatibility)
- React + Vite + Tailwind CSS setup with dark theme (`slate-950` background)
- Vite proxy config for `/api` and `/uploads`

### Phase 2 — Agents + Orchestrator
- `BaseAgent` abstract class
- All 5 agents written with LLaMA/Groq prompts (text + vision)
- `orchestrator.py` — async sequential pipeline, SSE event emission
- `useInvestigationSSE.js` hook on frontend

### Phase 3 — Full Frontend
- `ClaimsQueue` — table with skeleton rows, error banner, Decision column
- `NewClaim` — phone lookup → PolicyCard → incident form (NO manual policy/amount entry)
- `Dashboard` — 3-col top + 2-col bottom layout with live SSE
- `InvestigationWorkflow` — animated agent step pipeline
- `InvestigationSummary` — Recharts PieChart fraud donut + 4 check rows
- `ClaimDecision` — coloured badge + confidence bar
- `VisualEvidenceAnalysis` — canvas bounding box overlay
- `EvidencePanel` — 3 tabs (Evidence / Reasoning Trail / Timeline)

### Phase 4 — Polish & Demo Prep
- `Skeleton.jsx` — loading animations
- `ErrorBoundary.jsx` — React class component
- `NotFound.jsx` — 404 page
- `App.jsx` updated with ErrorBoundary + `*` route
- 3 seed claims with result.json (Approve / Escalate / Escalate)
- `README.md` with 5-step setup guide

### Phase 5 — RAG Knowledge Base
- Created `data/kb/fraud_indicators.json` (15 indicators, 5 schemes, IRDAI guidelines)
- Created `data/kb/vehicle_parts.json` (6 vehicles, OEM/aftermarket/labour pricing)
- Created `data/kb/claim_history.json` (20 historical cases)
- `scripts/generate_policy_pdfs.py` — argparse + fpdf2 PDF generator (✅ working)
- `scripts/build_vectorstore.py` — ChromaDB indexer using local ONNX embeddings (✅ no API calls)
- `backend/services/rag_client.py` — ChromaDB query service with 5 agent-specific helpers
- All 5 agents updated with RAG context injection before LLM prompt
- Updated `backend/config.py` with `KB_DIR`, `POLICIES_PDF_DIR`, `VECTORSTORE_DIR`
- `requirements.txt` updated: added `chromadb`, `fpdf2`, `PyMuPDF`, `groq`
- **RAG embeddings switched from Gemini API → local ONNX** (`all-MiniLM-L6-v2`): no rate limits, no API key needed for KB

### Phase 6 — Real-world Features (local, not on GitHub)
- **#2 Settlement transparency**: `services/settlement_calc.py` — deterministic IRDAI breakdown (repair − depreciation − deductible + GST = net payable); lazy backfill for existing claims; `SettlementBreakdown.jsx`
- **#1 Human-in-the-loop**: `storage.set_adjuster_decision()` + `add_adjuster_note()`; `POST /adjuster/decision` + `/adjuster/notes`; full status lifecycle (Approved/Rejected/Settled/Escalated/Pending Customer); `AdjusterPanel.jsx`
- **#6 Image quality gate**: vision model flags (blurry/dark/etc.) + deterministic checks in damage agent → `image_quality.gate_passed`; `ReviewBanner` in Dashboard
- **#6 Confidence-based routing**: any agent confidence < 60% → `needs_human_review = True` → status `Pending Review`; auto-routing logic in orchestrator
- **#7 Customer decision letter**: `services/letter_generator.py` + `GET /claims/{id}/letter`; adjuster decision takes precedence over AI; `DecisionLetter.jsx`

---

## Known Issues & Fixes Applied

| Issue | Root Cause | Fix Applied |
|---|---|---|
| `pip install` global pollution | No venv created first | Created `venv` before any pip installs |
| `npm create vite` failed | `frontend/` folder already existed | `Remove-Item -Recurse -Force frontend` first |
| `google.generativeai` FutureWarning | Deprecated SDK | Switched to Groq SDK; `gemini_client.py` is now a Groq wrapper (legacy filename kept) |
| `claims.csv` write blocked | File not read before Write tool | Read file first, then Write |
| UnicodeEncodeError in terminal | `→`, `✓` chars not in cp1252 | Replaced with `->`, `OK` in print statements |
| fpdf2 `FPDFUnicodeEncodingException` | Em-dash `—` not in Latin-1 | Added `_s()` sanitizer; all non-latin-1 → ASCII equivalents |
| fpdf2 `Not enough horizontal space` | `multi_cell(0,...)` in fpdf2 v2.7+ uses full page width from current x | Changed to explicit column widths (100mm value col, 162mm after numbered bullet) |
| `chunk_text` MemoryError | Infinite loop when `start = end - overlap` and `end == len(text)` | Added `if end >= text_len: break` |
| `AttributeError: 'str' has no .get` | `oem_authorised_dealer_premium` key in labour rates is a string not dict | Added `isinstance(info, dict)` check |
| `GeminiEmbeddingFunction` DeprecationWarning | ChromaDB v1.x requires `__init__` | Switched to ChromaDB's built-in ONNX `DefaultEmbeddingFunction` — no custom class needed |

---

## Current Status (as of session end)

| Component | Status |
|---|---|
| Backend (FastAPI, all routes) | ✅ Working |
| 5 AI agents (Groq LLaMA) | ✅ Working (with RAG injection, fallback if no vectorstore) |
| Frontend (React, all pages) | ✅ Working — production build verified (653 modules) |
| Policy PDFs (5 files) | ✅ Generated |
| `build_vectorstore.py` | ✅ Uses local ONNX — no API key needed |
| ChromaDB vectorstore | ⬜ Not yet built (run `python scripts/build_vectorstore.py`) |
| RAG queries in agents | ⬜ Will work automatically once vectorstore is built |
| #1 Adjuster decision + notes | ✅ Built (local, not pushed to GitHub) |
| #2 Settlement breakdown | ✅ Built (local, not pushed to GitHub) |
| #6 Image quality gate + confidence routing | ✅ Built (local, not pushed to GitHub) |
| #7 Customer decision letter | ✅ Built (local, not pushed to GitHub) |
| Analytics page | ✅ API endpoint exists; frontend page not yet built |
| Demo script / DEMO_SCRIPT.md | ⬜ Not started |

---

## Next Steps

1. **Build vectorstore:** `python scripts/build_vectorstore.py` (~30s, fully local)
2. **Smoke-test:** Run end-to-end investigation with Mohammed Faiz (new policy fraud demo)
3. **Demo prep:** Write DEMO_SCRIPT.md; enrich seed result.json with KB citation fields
