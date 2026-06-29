# ClaimIntel — AWS Deployment Architecture & Cost Model

> Companion to [CLAUDE.md](CLAUDE.md), which documents the current state: ClaimIntel runs 100% locally today (FastAPI backend, Groq-hosted LLMs, local ChromaDB/ONNX RAG, flat-file storage, Docker Compose). This document is the target-state plan for a real AWS deployment, **replacing Groq entirely with AWS Bedrock** and the local file-based storage with managed AWS services.

---

## 1. Overview

ClaimIntel is an agentic motor-insurance claim triage platform: a policyholder submits a claim, and 5 sequential AI agents (damage assessment, fraud intelligence, incident reconstruction, context verification, settlement recommendation) investigate it, grounded by a local RAG knowledge base. This doc covers:

1. A recommended AWS architecture — which managed service to use for each concern, and why.
2. A monthly AWS cost estimate at a stated "small production" scale.
3. A per-investigation-run cost breakdown, so the marginal cost of each claim investigated is known.

---

## 2. Scope & Assumptions

- **Scale:** 3,000 investigation runs/month, low-to-moderate concurrency. This is the working assumption for every estimate below — re-run the numbers if actual volume differs materially.
- **Region:** ap-south-1 (Mumbai). Chosen to match the India-focused insurance domain (INR pricing, IRDAI references) and to keep claim data + LLM inference in-region.
- **LLM policy:** Bedrock only — Groq is fully replaced. No external LLM API.
- **Out of AWS cost scope:** Nominatim (geocoding) and OpenWeatherMap (weather) remain external, free-tier dependencies regardless of this migration — they have no AWS-native equivalent worth adopting at this scale, so they're mentioned but not costed. An optional Serper/Tavily live-pricing lookup is the same.
- **Pricing basis:** on-demand, no Reserved/Savings Plan commitment — this scale doesn't yet justify a commitment discount.
- **⚠️ Pricing volatility disclaimer:** every dollar figure in this document is a researched-but-unverified estimate as of mid-2026. AWS Bedrock and other service pricing changes over time and varies by region. **Verify all figures in the AWS Pricing Calculator before committing budget.**
- **⚠️ Model availability:** ap-south-1 Bedrock model availability is broad as of mid-2026 (60+ models, including Amazon Nova, Meta Llama, and Anthropic via cross-region inference), but rollout varies by region and time — re-verify exact model IDs are available in ap-south-1 at deploy time.

---

## 3. Recommended Architecture

| Concern | AWS choice | Why |
|---|---|---|
| Backend compute | **ECS Fargate** (1 service, 0.5 vCPU / 1 GB, autoscale 1→3 tasks) | The backend is a long-lived FastAPI process streaming SSE events over a multi-minute, 5-agent sequential pipeline. Lambda's duration limits and awkward streaming-response support fight this pattern; Fargate reuses the existing container model with minimal rework. Not Lambda. |
| Frontend hosting | **S3 + CloudFront** | The frontend is already a static build (served by nginx today). S3+CloudFront drops an entire compute service for what is just static assets, and gets CDN caching + TLS for free. |
| LLM — vision agents (damage assessment, incident reconstruction) | **Bedrock: Amazon Nova Pro** ($0.80 / $3.20 per 1M in/out) | Multimodal, cheaper output than Claude 3.5 Haiku at similar input cost, and stronger reasoning than Nova Lite — appropriate for damage-authenticity and fraud-adjacent judgment calls these agents make. |
| LLM — text agents (fraud intelligence, settlement recommendation) | **Bedrock: Llama 3.3 70B Instruct** ($2.65 / $2.65 per 1M, flat) | Same model family/weights as today's Groq model — minimizes regression risk to the JSON-repair logic (`llm_client.ask_json`) and the 24 deterministic fraud checks / IRDAI settlement math that gate on LLM output structure. A cheaper Nova-family substitution is flagged as a phase-2 lever (§7) once output quality is validated in production. |
| RAG / vector store | **Chroma baked into the Fargate image** — no managed vector DB | The vectorstore is ~3.8 MB, read-mostly, and rebuilt offline (a build script, not a runtime mutation) — the same pattern the existing Dockerfile already uses to pre-bake the ONNX embedding model. OpenSearch Serverless's OCU floor (~$150–700+/month minimum) is wildly disproportionate to a few-MB read-mostly store. EFS-mounted Chroma is the fallback only if the knowledge base must be updated without a redeploy. |
| Claims/policy metadata | **DynamoDB**, on-demand mode — not RDS | Today's CSV storage uses a process-local file lock that breaks the moment more than one backend task exists. The access pattern is simple key lookups (by `claim_id`, by `phone`), not relational joins. DynamoDB on-demand has no idle fixed cost at this volume, unlike an always-on RDS instance. Revisit RDS only if Analytics-page reporting queries outgrow simple scans/GSIs. |
| Claim files (photos, PDFs, generated reports) | **S3**, lifecycle: Standard (0–90d) → Standard-IA (90d–1yr) → Glacier Instant Retrieval (1yr+) | Maps directly to the existing per-claim folder structure. Claim-data retention duration should be confirmed against IRDAI compliance norms before finalizing the lifecycle policy. |
| Secrets | **Secrets Manager** — OpenWeatherMap/Serper keys only | Bedrock authenticates via IAM, not an API key, so the Groq key is eliminated entirely — a security simplification worth noting on its own. |
| Networking | VPC (public + private subnets), **ALB**, **NAT Gateway** | ALB replaces today's direct port exposure; Fargate tasks sit in private subnets and need NAT to reach Nominatim/OpenWeatherMap/Bedrock. **NAT Gateway's fixed hourly charge (≈$32–37/month) is likely the single largest non-LLM cost line item** — see §7 for ways to reduce it. |
| Security / PII | **KMS** customer-managed key, replacing the local Fernet key | Envelope-encrypts the `claimant`/`phone` fields, enables SSE-KMS on S3, and encryption at rest on DynamoDB. |
| Observability | **CloudWatch** Logs + Container Insights + alarms (including a billing alarm, given Bedrock's usage-based pricing) | |

**Data residency note:** this migration keeps claim data and LLM inference entirely within ap-south-1 (Bedrock, S3, DynamoDB, KMS all in-region) — a meaningful improvement over today's documented gaps (Groq US, Nominatim EU, OpenWeatherMap US). Nominatim/OpenWeatherMap/Serper remain external by design and are not solved by this migration; that's a residual caveat, not an oversight.

### Architecture diagram (request flow)

```
 Browser ──HTTPS──► CloudFront + S3 (React SPA static assets)
                         │ API calls
                         ▼
                    ALB (public subnet)
                         │
        ┌────────────────────────────────────┐
        │  VPC private subnet                 │
        │  ECS Fargate: FastAPI task          │
        │   (backend + Chroma RAG baked in)   │
        │     │         │            │        │
        │     ▼         ▼            ▼        │
        │ DynamoDB    S3          Bedrock     │
        │ (claims/    (claim      (Nova Pro / │
        │  policy     files,       Llama 3.3  │
        │  metadata)  reports)     70B)       │
        └──────────────┬───────────────────────┘
                        │ NAT Gateway
                        ▼
      Nominatim (geocode) · OpenWeatherMap (weather)
      · optional Serper/Tavily (pricing) — external, non-AWS

 Secrets Manager → OpenWeatherMap/Serper keys
 KMS → encrypts DynamoDB PII fields, S3 objects
 CloudWatch → logs/metrics/alarms across all services
```

---

## 4. Per-AWS-Service Monthly Cost Table

Scale assumption: **3,000 investigation runs/month**, ap-south-1, on-demand pricing.

| Service | AWS Resource | Sizing / Config | Est. Monthly Cost (USD) | Notes |
|---|---|---|---|---|
| Backend compute | ECS Fargate | 1 task avg, 0.5 vCPU / 1 GB, ~730 hrs | **≈ $30** | $0.0463/vCPU-hr + $0.0051/GB-hr (ap-south-1 approx). Scales to 3 tasks under load. |
| LLM inference | Bedrock (Nova Pro + Llama 3.3 70B) | 3,000 runs × $0.0287/run (baseline, §5) | **≈ $87** | Cross-referenced from §5 — not independently estimated. |
| RAG / vector store | Chroma baked into image | ~3.8 MB, no managed service | **$0** | Marginal cost is zero; rebuilt offline and baked in at image build time. |
| Claims/policy metadata | DynamoDB on-demand | ~5 writes + 3 reads/run × 3,000 runs | **< $1** | Negligible at this volume. |
| Claim files | S3 (Standard → IA → Glacier lifecycle) | ~7 MB/claim × 3,000 claims/month, cumulative | **≈ $5** | Includes frontend static assets. |
| Frontend CDN | CloudFront | Low request volume at this scale | **≈ $2** | |
| Networking | ALB | Hourly + LCU | **≈ $20** | |
| Networking | NAT Gateway | Hourly + per-GB data processing | **≈ $35** | Flagged in §3 and §7 as the largest non-LLM fixed cost. |
| Secrets | Secrets Manager | 2–3 secrets (OpenWeatherMap, Serper) | **≈ $1.5** | |
| Encryption | KMS | 1 CMK + request volume | **≈ $1** | |
| Observability | CloudWatch (Logs + Container Insights + alarms) | Moderate log volume from 5-agent verbose orchestration | **≈ $10** | Tune retention — see §7. |
| **Total** | | | **≈ $190–195/month** | Excludes any AWS free-tier credits. |

---

## 5. Per-Investigation-Run Cost Breakdown

**Formula:**

```
Cost/run = (vision tokens × Bedrock vision-model rate)
         + (text tokens × Bedrock text-model rate)
         + (Fargate compute-seconds/run, amortized as monthly-cost ÷ monthly-run-count)
         + (S3 PUT + storage cost/run, amortized)
         + (DynamoDB read/write units/run × on-demand rate)
         + (external API calls — Nominatim/OpenWeatherMap — $0, free-tier, out of scope)
```

Compute is amortized as a monthly-total ÷ run-count rather than measured per-invocation, since the Fargate task runs continuously rather than being invoked per-request like Lambda — this is the simplest defensible amortization method.

**Token inputs** (real, measured from `data/token_usage.csv`, latest logged run):

| Agent | Type | Prompt (input) tokens | Completion (output) tokens |
|---|---|---|---|
| damage_assessment | Vision | 5,365 | 701 |
| incident_reconstruction | Vision | 9,763 | 440 |
| fraud_intelligence | Text | 2,341 | 200 |
| settlement_recommendation | Text | 1,928 | 428 |

(`context_verification` makes 0 LLM calls — pure geocode/weather/policy lookup.)

**Baseline LLM cost/run** (Nova Pro vision @ $0.80/$3.20 per 1M; Llama 3.3 70B text @ $2.65/$2.65 flat):

- Vision: input 15,128 tok, output 1,141 tok → (15,128/1e6 × 0.80) + (1,141/1e6 × 3.20) = **$0.01575**
- Text: input 4,269 tok, output 628 tok → (4,269 + 628)/1e6 × 2.65 = **$0.01298**
- **Baseline LLM cost/run ≈ $0.0287**

**Worst case** (+garage-estimate/FIR document parsing via vision LLM, ~5,000 in/600 out, +customer decision-letter text generation, ~800 in/150 out):

- Extra vision: (5,000/1e6 × 0.80) + (600/1e6 × 3.20) = $0.00592
- Extra text: (800 + 150)/1e6 × 2.65 = $0.00252
- **Worst-case LLM cost/run ≈ $0.0372**

**Other per-run components** (amortized at 3,000 runs/month):

- Fargate compute: $30/month ÷ 3,000 runs ≈ **$0.01/run**
- S3: ~7 MB/claim, negligible per-run (**$0.0002/run**) — cumulative storage growth is tracked in §4's monthly total, not here.
- DynamoDB: ~5 writes + 3 reads/run on-demand ≈ **$0.00001/run** (negligible).

**Total cost per run:**

| Scenario | Cost/run |
|---|---|
| Baseline (5 agents, no optional docs/letter) | **≈ $0.039** |
| Worst case (+document parsing +decision letter) | **≈ $0.048** |

**Monthly total at 3,000 runs/month:** 3,000 × $0.039 ≈ **$117** total run-driven cost (LLM + amortized compute/storage/DB) — consistent with §4's LLM line item of ~$87/month plus the $30/month Fargate line, confirming the two sections agree.

---

## 6. Model Comparison Table (Bedrock options)

| Model | Vision-capable | $/1M input | $/1M output | Recommended for | Why / why not |
|---|---|---|---|---|---|
| **Amazon Nova Pro** | ✅ | $0.80 | $3.20 | **Vision agents (chosen)** | Best cost/quality balance for damage-authenticity and fraud-adjacent judgment calls; cheaper output than Claude Haiku at similar input cost. |
| **Llama 3.3 70B Instruct** | ❌ | $2.65 | $2.65 | **Text agents (chosen)** | Same model family as today's Groq model — lowest regression risk to existing JSON-repair logic and deterministic fraud/settlement checks. |
| Amazon Nova Lite | ✅ | $0.06 | $0.24 | Cost-aggressive alternative for vision agents | Far cheaper, but reasoning quality is a step down from Nova Pro — riskier for authenticity/fraud judgment calls. Worth a quality bake-off before adopting. |
| Amazon Nova Micro | ❌ | $0.035 | $0.14 | Not recommended here | Text-only and the cheapest option, but no vision support and the cheapest end of the quality spectrum — too aggressive a cut for settlement-affecting reasoning. |
| Llama 4 Scout 17B | ✅ | $0.35 | $1.00 | Alternative vision option | Cheaper than Nova Pro; same model family as today's Groq vision model. Worth bake-off against Nova Pro if Groq-parity matters more for vision than for text. |
| Claude 3.5 Haiku | ✅ | $0.80 | $4.00 | Alternative vision option | Comparable input cost to Nova Pro but pricier output; strong reasoning, but no clear advantage over Nova Pro for this workload at higher cost. |
| Claude 3.5 Sonnet | ✅ | $3.00 | $15.00 | Not recommended at this scale | Best-in-class reasoning, but ~5x Nova Pro's cost — overkill for a "small production" deployment; consider only if Nova Pro/Llama 3.3 quality proves insufficient in production. |

**Phase-2 lever:** once output quality from Llama 3.3 70B is validated in production, evaluate substituting Nova Pro (or even Nova Lite) for the text agents too — Nova Pro text-only calls (~$0.80/$3.20) would meaningfully undercut Llama 3.3 70B's flat $2.65/$2.65, but this isn't recommended for the initial migration to avoid compounding model-change risk with infrastructure-change risk.

---

## 7. Cost Optimization Levers

- **Bedrock prompt caching** — if/when supported for Nova Pro / Llama 3.3 70B, can cut repeated-context costs (e.g. shared KB context across calls) by up to ~90% on cached input tokens. Verify support for the chosen models before relying on this.
- **Fargate Spot** for scale-out tasks (2nd/3rd autoscaled task) — not the baseline task, since claim investigation is user-facing and latency-sensitive.
- **S3 lifecycle / Intelligent-Tiering** — already factored into §3/§4; Intelligent-Tiering is a lower-maintenance alternative to manually staged lifecycle rules if claim-access patterns are unpredictable.
- **DynamoDB on-demand → provisioned + reserved capacity** — revisit if monthly run volume grows well past 3,000/month; on-demand is the right choice at "small production" scale but provisioned+reserved becomes cheaper at sustained high throughput.
- **VPC Gateway Endpoints** (free) for S3 and DynamoDB traffic — removes that traffic from the NAT Gateway's per-GB data-processing charge.
- **Bedrock PrivateLink interface endpoint** for Bedrock traffic — has its own hourly + per-GB cost, so compare against NAT's per-GB cost for the (small) volume of Bedrock traffic before adopting; likely not worth it at this scale, but worth a one-time calculation.
- **Nova-family substitution for text agents** (see §6 phase-2 lever) — meaningful LLM cost reduction once quality is validated.
- **CloudWatch log retention/sampling** — the 5-agent orchestration is verbose; setting a shorter retention period (e.g. 30 days instead of indefinite) and/or sampling debug-level logs reduces the ingestion/storage line item in §4.
- **Batch inference** — not applicable here: the app's synchronous SSE-streamed investigation flow has no batch/offline workload to apply batch discounts to.

---

## 8. Assumptions & Caveats

- All Bedrock and AWS service pricing in this document is researched but unverified against the live AWS Pricing Calculator at time of writing (mid-2026) — **re-verify before committing budget**, as pricing is volatile and varies by region.
- ap-south-1 Bedrock model availability is broad as of mid-2026 but subject to change — re-verify exact model IDs are available in-region at deploy time.
- NAT Gateway (≈$35/month) is a disproportionately large fixed cost for a "small production" deployment at this scale — see §7 for mitigation options.
- Nominatim, OpenWeatherMap, and the optional Serper/Tavily pricing lookup remain non-AWS, external dependencies with their own rate limits and terms of service — not covered by this AWS cost model, and not solved by this migration.
- Token counts are measured from actual logged investigation runs in this codebase (`data/token_usage.csv`), not synthetic estimates — but they reflect today's prompt sizes; prompt engineering changes during the Bedrock migration (e.g. adapting prompts for Nova Pro vs. Groq's Llama 4 Scout) could shift token counts up or down.
- DynamoDB/S3/Fargate cost estimates assume the stated 3,000 runs/month scale; re-run all calculations in §4 and §5 if actual production volume differs materially.
- No Reserved Instance, Savings Plan, or Bedrock commitment-tier discounts are assumed — all figures are on-demand pricing, appropriate for this stage but worth revisiting once volume and usage patterns stabilize.
