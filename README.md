# ClaimIntel — Agentic Motor Insurance Claim Investigation Platform

An AI-powered motor-insurance claim triage system that replaces manual investigation with a pipeline of **5 specialized AI agents**, a local **RAG knowledge base**, **24 deterministic fraud checks**, transparent **IRDAI-aligned settlement math**, and a **human-in-the-loop** adjudicator workflow — running entirely on a laptop.

> No cloud, no database, no managed deployment. Free APIs only: Groq for inference, Nominatim for geocoding, OpenWeatherMap optional.

---

## What it does

- **Phone-based intake** — the policyholder enters a phone number and the policy auto-fills. No manual policy or amount entry; the claim amount is set by the AI after damage assessment.
- **5-agent investigation pipeline** — vision damage assessment, fraud scoring, incident reconstruction, geo/policy verification, and a final settlement recommendation, streamed live to the UI over SSE.
- **24 deterministic fraud checks (FC-01…FC-24)** — every claim is run through an explicit checklist, each recorded as **pass / flag / N/A** and shown in a per-agent **Fraud Check List** panel. A Python-authoritative score drives the Low/Medium/High label.
- **Multi-source evidence cross-checks** — uploaded **FIR** and **garage-estimate** PDFs are parsed (vision LLM) and cross-checked against the claim; **telematics/IoT** data and **dash-cam video** (auto-split into frames) corroborate or contradict the story.
- **Cross-claim garage intelligence (FC-24)** — workshops are scored across all prior claims for systematic estimate inflation (FS-004), surfaced in a **Garage Risk Intelligence** analytics table.
- **Transparent, IRDAI-aligned settlement** — an itemised breakdown: repair basis − material-wise depreciation − deductibles (− salvage on total loss), with GST treated as already included.
- **Anti-inflation cap** — when a garage quote exceeds the AI-assessed fair value by **more than 40%**, the payout is computed on the **AI fair value** and the disallowed excess is booked as **leakage prevented**.
- **Human-in-the-loop** — every AI decision is a *recommendation*; an adjudicator approves, rejects, escalates, or requests info, with a notes thread.
- **Deterministic guardrails** — hard rules override the LLM (policy expiry → reject; damage-vs-story mismatch → block auto-approval → escalate; low confidence / failed image gate → human review).
- **Explainable outputs** — full reasoning trail, KB citations, a downloadable PDF report (now including the settlement breakdown), and an auto-drafted customer decision letter.
- **Analytics & governance** — fraud trends, settlement exposure, leakage prevented, garage risk, and AI ↔ human agreement metrics, plus Groq token-usage observability against the free-tier caps.

---

## Architecture

```
Policyholder files a claim (phone lookup → policy auto-fill → photos + FIR/estimate/telematics/dash-cam)
        ↓
┌──────────────────────────────────────────────────────────────────────┐
│  KNOWLEDGE BASE (RAG — built once, queried per agent)                 │
│  ├─ fraud_knowledge   : 15 fraud indicators + 5 known schemes         │
│  ├─ vehicle_catalog   : OEM / aftermarket parts pricing               │
│  ├─ claim_history     : 20 historical claims with outcomes            │
│  └─ policy_documents  : customer policy PDFs                          │
│        ChromaDB + local ONNX embeddings (all-MiniLM-L6-v2, offline)   │
└───────────────────────────┬──────────────────────────────────────────┘
                            │  similarity search per agent
                            ▼
 A1 Damage Assessment       — Groq Vision + vehicle_catalog → repair estimate, image-quality + authenticity gate
 A2 Fraud Intelligence      — 24 deterministic checks (FC-01…FC-24) + fraud KB → fraud score 0–100
 A3 Incident Reconstruction — Groq Vision (photos + dash-cam frames) + claim_history → collision type, damage-vs-story match
 A4 Context Verification    — Nominatim geocode + telematics GPS cross-check + OpenWeatherMap + policy KB → eligibility
 A5 Settlement Recommendation — all findings + precedents → Approve / Reject / Escalate
        ↓
 Deterministic guards + IRDAI settlement math (+ inflation cap)  →  Human adjudicator review  →  Final decision
        ↓
 Live SSE stream updates the UI as each agent completes
```

---

## The settlement principle (important)

**Fraud detection and payout calculation are deliberately separate concerns**, with one well-defined exception (the inflation cap below).

Settlement is itemised and deterministic (no LLM), IRDAI-aligned:

```
Net Payable = Approved Repair Basis − Depreciation (material-wise) − Deductibles (− Salvage on total loss)
```

- GST is treated as **already included** in the garage total (never added on top).
- A constructive-total-loss path settles on **IDV − salvage** when repair ≥ 75% of the sum insured.

**Fair-Value Analysis** compares the garage quote to the AI's independent estimate and assigns a band:

| Variance (garage vs. assessed) | Band | Recommended action | Effect on payout |
|---|---|---|---|
| within ±20% | Consistent | Auto Process | Repair basis = garage quote |
| +20% to +40% | Elevated | Review Required | Repair basis = garage quote (flagged for review) |
| **> +40%** | **Inflated** | **Investigation Required** | **Repair basis capped at the AI fair value; excess = leakage prevented** |

Only the **Inflated (>40%)** band changes the payout. For consistent/elevated claims the human-trusted garage quote remains the basis; the analysis is a fraud signal that routes the workflow.

---

## Quick Start (Docker — recommended)

**Prerequisites:** Docker Desktop running.

```bash
# 1. Configure your API key
cp .env.example .env
# Edit .env — add GROQ_API_KEY (free at console.groq.com). OPENWEATHERMAP_API_KEY is optional.

# 2. Start everything
docker compose up --build

# 3. Open the app  → http://localhost:3000
```

`data/` is bind-mounted, so claims, uploads, the vectorstore, and results persist across restarts. The backend is also exposed directly at `http://localhost:8001` for curl/Postman.

### Build the Knowledge Base (RAG — richer, citation-backed outputs)

```bash
docker compose exec backend python /app/scripts/build_vectorstore.py
```

> The app works without this — agents gracefully fall back to reasoning without KB context. Policy PDFs are pre-generated in `data/kb/policies/`.

### Stop / restart

```bash
docker compose down          # stop (data persists)
docker compose up            # restart without rebuild (fast)
docker compose up --build    # rebuild after code changes
```

---

## Manual Setup (local dev, no Docker)

```bash
# 1. API keys
cp .env.example .env          # fill in GROQ_API_KEY (OPENWEATHERMAP_API_KEY optional)

# 2. Python deps (from project root)
python -m venv venv
venv\Scripts\pip install -r requirements.txt          # Windows
# source venv/bin/activate && pip install -r requirements.txt   # macOS/Linux

# 3. Node deps
cd frontend && npm install && cd ..

# 4. Build the KB (one-time, ~30s, fully local)
python scripts/build_vectorstore.py

# 5. Run (two terminals)
#  Backend (from /backend):
..\venv\Scripts\uvicorn main:app --reload --port 8001
#  Frontend (from /frontend):
npm run dev
```

Open **http://localhost:5173**.

---

## Roles & Login

Two demo roles (click-to-fill on the login screen):

| Role | Credentials | Can do |
|---|---|---|
| 👤 **Policyholder** | `user` / `user` | File a claim, see submission confirmation |
| 🛡 **Adjudicator** | `adjudicator` / `adjudicator` | Claims queue, run investigations, make decisions, analytics |

Investigation and decision routes are role-guarded on both the frontend and the API (`X-Role` header). **Run investigations as the adjudicator.**

---

## Demo Walkthrough

The claims queue starts empty — you file claims live and watch the agents run. Ready-to-use evidence for three showcase cases is generated into [`test/`](test/) by:

```bash
python scripts/generate_demo_assets.py
```

This writes a self-documenting tree with per-case filing steps in [`test/README.md`](test/README.md). You supply the real **damage photos** and a **dash-cam .mp4**; everything else (garage estimates, FIR, telematics JSON) is generated.

| Case | Customer (phone) | Evidence in `test/` | You add | Expected outcome |
|---|---|---|---|---|
| **A — Genuine** | Rajesh Kumar (`9876543210`) | consistent estimate + genuine telematics | damage photos | **Approve** — low fraud, all checks pass/na, GPS verified |
| **B — Inflation + FIR mismatch** | Meera Iyer (`8890123456`) | inflated estimate + mismatched FIR | front-damage photos | **Escalate** — FC-17 inflation (FS-004) + FC-20 FIR date/location mismatch + settlement **inflation cap** |
| **C — Staged collision** | Arjun Mehta (`9900112233`) | consistent estimate + fraud telematics | dash-cam .mp4 | **Escalate** — FC-21 no-impact mismatch + FC-22 GPS mismatch + FC-02 prior claim |

**Steps:** log in as **adjudicator** → *New Claim* → enter the phone (policy auto-fills) → fill incident details per `test/README.md` → upload the listed files (**leave the garage-amount field blank** so the estimate PDF drives the figure) → open the claim → **Start Investigation** → watch the 5 agents stream → use the adjuster panel to decide, download the PDF report, or draft the customer letter.

---

## Decision logic & guardrails

The Agent-5 LLM only **recommends**. Deterministic rules override it:

- **Policy expired / pre-inception** → forced **Reject** (coverage is a binary fact).
- **Damage doesn't match the story** (reconstruction) → auto-approval **blocked → Escalate**.
- **Low agent confidence or failed image-quality / authenticity gate** → routed to **Pending Review**.
- **High-value Escalate** (above the surveyor threshold) → **Survey Required**.

The fraud label is derived from the Python-authoritative score (`<40 Low · 40–69 Medium · 70+ High`) so it always agrees with the Approve / Escalate / Reject bands.

---

## Data, privacy & security

- **Third-party transmission:** agent calls send claim data to Groq (US); Context Verification sends `incident_location` to Nominatim and (optionally) OpenWeatherMap. There is no on-prem LLM in this build.
- **PII masking before LLM calls** — `services/pii.py` masks `phone` and `claimant` for the two agents that embed the full claim in their prompt.
- **Encryption at rest** — `services/crypto.py` (Fernet/AES via `ENCRYPTION_KEY`) transparently encrypts the `claimant` and `phone` columns in `claims.csv`. Without a key the app still runs (plaintext).
- **Token observability** — `services/token_tracker.py` logs every Groq call to `data/token_usage.csv` (git-ignored), surfaced on the Analytics page against the free-tier caps.
- **Photo authenticity** — the damage agent flags AI-generated / stock / manipulated / screenshot images, corroborated by a local EXIF check.

---

## Tech Stack

| Layer | Technology |
|---|---|
| AI / LLM | Groq — Llama 3.3 70B (text) + Llama 4 Scout 17B (vision) |
| RAG | ChromaDB + local ONNX `all-MiniLM-L6-v2` embeddings (fully offline) |
| Backend | FastAPI + Python 3.10+, SSE for live agent streaming |
| Frontend | React 19 + Vite + Tailwind CSS 4 + Recharts |
| Storage | Local CSV + JSON files (no database) |
| Geocoding / Weather | Nominatim (no key) + OpenWeatherMap (optional) |
| PDF | fpdf2 (generation) · PyMuPDF/fitz (parsing) · OpenCV (dash-cam frames) |
| Container | Docker Compose + nginx |

---

## Project Structure

```
backend/
  main.py                  FastAPI app + all routes (role-guarded investigate/decision)
  orchestrator.py          runs A1→A5, parses docs/telematics/dash-cam, guards, SSE, result.json
  storage.py               CSV/JSON I/O, claim-ID generation, PII encryption hooks
  agents/                  5 agents (damage, fraud, reconstruction, context, settlement)
  services/
    llm_client.py          Groq SDK wrapper (formerly gemini_client.py)
    rag_client.py          ChromaDB query service (graceful fallback)
    settlement_calc.py     IRDAI settlement + fair-value analysis + inflation cap
    garage_intel.py        cross-claim garage inflation scoring (FC-24 + analytics)
    document_parser.py     FIR / garage-estimate PDF parsing (vision LLM)
    telematics_parser.py   telematics/IoT JSON-CSV ingestion
    video_parser.py        dash-cam video → still frames (OpenCV)
    report_generator.py    explainable PDF investigation report
    letter_generator.py    customer decision letter
    pii.py / crypto.py     PII masking + at-rest encryption
    token_tracker.py       Groq token-usage logging
frontend/src/
  pages/                   Login, ClaimsQueue, NewClaim, Dashboard, Analytics, NotFound
  components/              InvestigationWorkflow (+ Fraud Check List), SettlementBreakdown,
                           EvidencePanel, AdjusterPanel, DecisionLetter, VisualEvidenceAnalysis, …
data/
  policies.csv             15 demo policies (phone → policy lookup)
  claims.csv               claim records (created at runtime; ships empty)
  kb/                      fraud indicators, vehicle parts, claim history, policy PDFs, sample telematics
  vectorstore/             ChromaDB persistent store (built by script; git-ignored)
scripts/
  generate_policy_pdfs.py      synthetic policy PDFs
  build_vectorstore.py         index the KB into ChromaDB (local ONNX)
  generate_sample_telematics.py  sample telematics JSON
  generate_demo_assets.py      the 3-case showcase fixtures → test/
test/                      showcase demo assets + per-case README (you add photos / dash-cam)
```

---

*Runs entirely on a laptop. No cloud, no database, no external deployment needed.*
