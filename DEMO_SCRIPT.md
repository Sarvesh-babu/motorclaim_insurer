# ClaimIntel — Ideathon Demo Script
_For judges and live demo walkthrough_

---

## Setup (Before Demo)

```bash
# Terminal 1 — Backend
cd backend
..\venv\Scripts\uvicorn main:app --reload --port 8000

# Terminal 2 — Frontend
cd frontend
npm run dev
```

Open: **http://localhost:5173**

---

## 1. Opening Hook (30 seconds)

> "Manual motor insurance claim investigation takes 3–7 days and costs Rs.2,500 per claim in surveyor fees.
> ClaimIntel replaces that with 5 specialized AI agents that complete a full investigation in under 60 seconds —
> grounded by a real institutional knowledge base of fraud indicators, historical cases, and policy documents."

**Show:** The Claims Queue page with 3 pre-seeded claims showing different outcomes.

---

## 2. Pre-Seeded Claims Walkthrough (2 minutes)

### Claim 1 — Rajesh Kumar (CLM-2025-05-000001) → APPROVE
- Click the claim → Dashboard opens
- Point to: **Fraud score 14% (Low)**, green Approve badge
- Reasoning trail: *"All four agent outputs align: genuine damage, low fraud risk, 89% confidence reconstruction"*
- **KB citation:** *"HIST-001 — same Maruti Swift, traffic signal collision, Approved Rs.45,000"*

**Talking point:** "The agent didn't just guess — it found the closest historical precedent and anchored its decision."

---

### Claim 2 — Mohammed Faiz (CLM-2025-05-000002) → ESCALATE (Fraud Demo)
- Click the claim → Dashboard opens
- Point to: **Fraud score 76% (High)**, red Escalate badge
- **KB citations to highlight:**
  - *"FI-POL-001: New Policy Syndrome — Risk Weight 85/100"* — policy only 47 days old
  - *"FS-001: New Policy Pre-Existing Damage Fraud"* — exact scheme match
  - *"HIST-004, HIST-010 — both rejected Toyota Innova / Honda City new-policy claims"*
- Policy document excerpt: *"Pre-existing damage exclusion applies. Surveyor mandatory above Rs.50,000."*

**Talking point:** "This is the wow moment. Agent 2 didn't just flag the policy age — it matched it against a known fraud scheme from our institutional KB and cross-referenced two rejected precedents."

---

### Claim 3 — Priya Sharma (CLM-2025-05-000003) → ESCALATE (Mixed Signals)
- Click the claim → Dashboard opens
- Point to: **Fraud score 38% (Medium)**, amber Escalate badge
- **KB citation:** *"FI-DMG-002: Undisclosed secondary damage — investigation action: request full disclosure"*
- Reasoning trail shows **conditional** settlement: *"Rs.58,000 approved pending windshield clarification"*

**Talking point:** "Not every escalation is fraud. Sometimes it's just ambiguity that needs one more question. The agent knew the difference."

---

## 3. Live Claim Filing (3 minutes)

### Step 1 — Phone Lookup
- Go to **New Claim**
- Enter phone: `9988776655` (Mohammed Faiz — fraud demo customer)
- Click "Find Policy" → PolicyCard auto-fills: name, vehicle (Toyota Innova Crysta), policy number, coverage

**Talking point:** "No manual policy entry. We look up the customer by phone — same as how a real call centre works."

### Step 2 — Fill Incident Details
- Claim Type: Own Damage
- Date: today
- Location: `Outer Ring Road, Chennai`
- Description: `Vehicle was parked outside my office. Came back to find left side severely dented by a passing vehicle. No witnesses.`
- Skip photo upload (or upload any sample image)

### Step 3 — Submit & Investigate
- Click Submit → redirected to Dashboard
- Click **Start Investigation**
- Watch the **5-agent pipeline animate** in real-time:
  - Agent 1 dots turn green one by one
  - SSE stream pushes live status

**Talking point:** "Each agent runs sequentially. Agent 2 is querying our fraud KB right now..."

### Step 4 — View Live Results
- Watch final decision appear: **Escalate, Fraud 76% High**
- Scroll to Reasoning Trail — point out KB indicator IDs (`FI-POL-001`, `FS-001`)

---

## 4. Architecture Callout (1 minute)

Point to the **KB Grounded badge** (top-right of Investigation Pipeline):

> "That green badge means all 5 agents are running with 100 vectors of institutional knowledge —
> fraud indicators, 20 historical cases, 6 vehicle pricing catalogs, and 5 real policy documents.
> All local. No extra API calls. Offline after first setup."

Diagram talking points:
- **ChromaDB** — local vector DB, similarity search per agent
- **ONNX all-MiniLM-L6-v2** — local embedding, no API rate limits
- **Groq (Llama 3.3 70B + Llama 4 Scout vision)** — only used for reasoning + vision analysis, not embedding
- **Graceful fallback** — agents still work if KB not built

---

## 5. Key Differentiators (30 seconds)

| Feature | Typical Solution | ClaimIntel |
|---|---|---|
| Grounding | Hallucinated facts | Institutional KB (100 vectors) |
| Fraud detection | Rule-based only | RAG + AI + IRDAI-aligned scoring |
| Policy lookup | Manual entry | Phone → auto-fill |
| Speed | 3-7 days | <60 seconds |
| Cost | Rs.2,500/claim surveyor | Free API tier |
| Setup | Cloud infra | 100% laptop, offline-capable |

---

## 6. Q&A Prep

**"How does the RAG KB stay up to date?"**
> "Run `python scripts/build_vectorstore.py` to re-index. New fraud cases, updated pricing, new schemes — all hot-swappable without changing agent code."

**"What if Groq API is down?"**
> "Agents have a graceful fallback. The KB queries are local and always work. Groq is only needed for the reasoning step."

**"How accurate is the fraud scoring?"**
> "Scoring thresholds are IRDAI-aligned: 0-30 Low, 31-59 Medium, 60-79 High, 80-100 Critical. The KB indicators each carry a calibrated risk weight from real insurance industry guidelines."

**"Can it handle real claim photos?"**
> "Yes — Groq's Llama 4 Scout vision model analyzes uploaded images. The damage bounding boxes and severity assessments in the demo are from real vision analysis."

---

## Demo Flow Timing

| Segment | Time |
|---|---|
| Opening hook | 30s |
| Pre-seeded claims x3 | 2 min |
| Live claim filing | 3 min |
| Architecture callout | 1 min |
| Differentiators | 30s |
| **Total** | **~7 minutes** |

---

## Emergency Fallbacks

- **Backend won't start:** `cd backend && ..\venv\Scripts\python -m uvicorn main:app --port 8000`
- **No internet (Groq down):** Pre-seeded claims still show full results — use those for the demo
- **KB badge shows red:** `python scripts/build_vectorstore.py` (takes ~30s, local ONNX)
- **Frontend blank:** Hard refresh, check Vite proxy is running on port 5173
