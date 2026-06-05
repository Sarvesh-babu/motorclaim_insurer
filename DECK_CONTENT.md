# Final Deck — Content (Story + Evaluation Coverage)

> Slide-ready content for **ClaimIntel — Agentic Insurance Claim Triage & Assessment**.
> Each slide notes the evaluation parameter(s) it answers. One-line *speaker cue*
> per slide drives the story. Tech-architecture slide already exists — this is the
> story around it.

**Narrative spine:** *A claim walks in → 5 AI agents investigate it in minutes →
a human signs off with full evidence → the business saves money and time.*

---

## Slide A — The Problem, Quantified  *(covers #1, #3)*
*Speaker cue: "Every motor claim today is a slow, manual, opaque investigation."*

- A single motor claim is reviewed **manually** across photos, FIRs, garage bills and customer narratives.
- **4+ siloed teams** (adjuster, surveyor, fraud, settlement) → **7–15 day** settlement cycle.
- Fraud detection is **rule-based** — misses staged accidents, workshop inflation, pre-existing damage.
- Result: **claims leakage** (overpaid/fraudulent claims), inconsistent decisions, poor customer experience, no audit trail.
- The gap isn't digitisation — it's the **absence of reasoning**: no system that *investigates* a claim end-to-end.

---

## Slide B — The Claim's Journey (How It Works)  *(covers #5, #7)*
*Speaker cue: "Watch one claim flow through the pipeline."*

A claim is investigated by **5 sequential AI agents + an orchestrator**, each building on the last:

1. **Damage Assessment** (Vision) — detects damaged parts, severity, repair type; draws bounding boxes on the photo.
2. **Fraud Intelligence** — 15 indicators + 5 known schemes + rule flags (new-policy syndrome, late reporting, inflation) → calibrated fraud score.
3. **Incident Reconstruction** (Vision) — does the visible damage match the story? Confidence-scored.
4. **Context Verification** — geolocation, weather, and policy coverage validation.
5. **Settlement Recommendation** — decision + transparent, IRDAI-aligned payout math.

→ Every step writes a **plain-English reasoning trail** with evidence citations.

---

## Slide C — From Idea to Working Prototype  *(covers #4)*
*Speaker cue: "This isn't a mockup — it's a running product."*

**Fully functional, end-to-end app — runs today on a laptop:**

- Two-role experience: **Policyholder** files a claim → **Adjudicator** investigates & decides.
- Live **agent pipeline** with real-time streaming status (SSE).
- **Visual evidence** with AI bounding-box overlays; document parsing (estimate / FIR).
- **Human-in-the-loop**: adjuster approve / reject / override + notes thread + decision lifecycle.
- **Auto-drafted customer decision letter** (one click).
- **Analytics dashboard** for the claims desk.
- **Dockerised** (`docker compose up`) — backend + frontend + knowledge base.

---

## Slide D — Innovation & Differentiation  *(covers #5)*
*Speaker cue: "Three things no off-the-shelf claims system does."*

- **Multimodal evidence fusion** — correlates photos ↔ narrative ↔ policy ↔ external data in *one* investigation, to catch contradictions.
- **Deterministic pricing engine** — the LLM only *classifies* damage; a rules engine prices it (segment-aware catalog + **live web prices**, depreciation, GST). No hallucinated rupee figures.
- **Trust by design** — confidence-based routing: low-confidence or poor-photo claims are auto-routed to a human instead of being auto-decided.
- **Grounded, not guessed** — every agent is backed by a local RAG knowledge base (fraud schemes, parts pricing, 20 historical precedents, policy docs).

---

## Slide E — GenAI Under the Hood  *(covers #6)*
*Speaker cue: "Modern agentic GenAI — and it runs free."*

- **Agentic architecture** — orchestrator coordinates 5 specialist agents (planning, routing, context, aggregation).
- **Multimodal LLMs** — vision model for damage/scene reasoning, text model for fraud & settlement reasoning.
- **RAG grounding** — local vector store (ChromaDB + on-device embeddings); each agent retrieves domain knowledge before reasoning.
- **Prototype runs on free tier** (Groq LLaMA + local embeddings) → **production maps 1:1 to AWS Bedrock / Rekognition / Textract** (see arch slide).
- Structured JSON outputs + self-repair for reliable, parseable agent results.

---

## Slide F — Explainability & Trust  *(covers #7)*
*Speaker cue: "Every rupee and every verdict is defensible."*

- **Plain-English rationale** for every decision, with direct evidence citations (photo finding, indicator ID, policy clause).
- **Transparent settlement sheet** — repair − depreciation − deductible + GST = net payable. No black box.
- **Fair-value audit** — garage quote vs independently assessed value; flags & caps workshop inflation.
- **Human governance** — adjuster confirms/overrides; every override is recorded → full **audit trail** for regulators & disputes.
- **Confidence + image-quality gates** — the system knows when *not* to decide.

---

## Slide G — Business Impact & Value  *(covers #3)*
*Speaker cue: "It pays for itself — and we measure it."*

- **Turnaround:** 7–15 days → **minutes** for AI triage, **hours** end-to-end with human sign-off.
- **Claims leakage prevented** — quantified live: rejected claims + inflation trimmed.
- **Fraud exposure stopped** — ₹ value of high-risk claims blocked before payout.
- **Consistency** — same evidence → same decision, every time (vs variable adjuster judgement).
- **Built-in management dashboard:** leakage prevented, deductions recovered, **AI–human agreement rate**, fraud risk mix, settlement distribution.

---

## Slide H — Scalability & Production Readiness  *(covers #8)*
*Speaker cue: "From ideathon laptop to enterprise — same design."*

- **Modular agents** — add/swap an agent (e.g. legal, subrogation) without touching the rest.
- **Stateless services + containerised** — horizontally scalable; queue-driven batch investigation.
- **Cloud-native path** — local prototype → AWS Bedrock (LLM), Rekognition/Textract (vision/OCR), S3 + DynamoDB, Step Functions (orchestration).
- **Graceful degradation** — works even if the knowledge base or external APIs are unavailable.
- **Configurable & auditable** — tunable thresholds, full logs; ready for regulatory & PII controls in production.

---

## Slide I — Applicability to Ganit  *(covers #2)*
*Speaker cue: "One pattern, many clients."*

- **Direct BFSI / Insurance offering** — motor today; extends to health, property, travel claims.
- **Reusable agentic + RAG framework** — any document-heavy, multimodal investigation: KYC, loan underwriting, invoice/expense audit, warranty claims.
- **Client-ready accelerator** — shortens Ganit POCs from weeks to days with a proven agent-orchestration template.
- **Showcases Ganit's edge** — hypotheses-led analytics + GenAI + multimodal reasoning in one tangible asset.

---

## Optional closing line
*"ClaimIntel turns claim **processing** into claim **investigation** — autonomous, explainable, and built to scale."*
