# ClaimIntel — Reality & Bug Audit

> Full pass over the codebase looking for (a) actual bugs, (b) things that don't
> match how real motor insurance works, and (c) production gaps. Each item has a
> severity, where it lives, why it matters, and a suggested fix.
> Severity: 🔴 bug / wrong output · 🟠 real-world inaccuracy · 🟡 production gap · ⚪ minor

---

## Priority summary

| # | Issue | Severity | Area |
|---|---|---|---|
| 1 | `claim_amount` not propagated to agents → amount-based fraud flags never fire | 🔴 | Orchestrator/Fraud |
| 2 | Eligibility failures (expired policy) left to LLM, no hard reject | 🔴 | Decision logic |
| 3 | `claim_amount` stored as **min** estimate (undervalues) | 🟠 | Orchestrator |
| 4 | No total-loss / IDV path (theft & write-offs pay ₹0 or repair-only) | 🟠 | Settlement |
| 5 | Net payable not capped at Sum Insured / IDV | 🟠 | Settlement |
| 6 | Depreciation applied to **all** parts equally (glass should be 0%) | 🟠 | Settlement |
| 7 | Zero-Depreciation add-on not modelled | 🟠 | Settlement |
| 8 | GST only on labour; parts GST & voluntary deductible ignored | 🟠 | Settlement |
| 9 | Compulsory deductible hardcoded ₹1,000 (should vary by engine cc) | 🟠 | Settlement |
| 10 | Surveyor threshold inconsistent (₹2,00,000 vs ₹50,000) | 🟠 | Fraud/KB |
| 11 | Fraud **score** is LLM-generated → not reproducible | 🟠 | Fraud |
| 12 | `traffic: "Moderate"` hardcoded, shown as real data | 🟠 | Context |
| 13 | Third-party / theft claim types not truly handled | 🟠 | Pipeline |
| 14 | NCB (No-Claim Bonus) impact not modelled | 🟠 | Settlement |
| 15 | Backend has no auth (frontend-only gate) | 🟡 | Security |
| 16 | CSV read-modify-write not concurrency-safe | 🟡 | Storage |
| 17 | Re-investigation keeps stale adjuster decision/notes | 🟡 | Storage |
| 18 | SSE queues never cleaned up (slow memory growth) | ⚪ | API |

---

## 🔴 Functional bugs

### 1. `claim_amount` never reaches the agents → key fraud rules silently dead
After Agent 1, the estimate is written to the **CSV** but **not** to the in-memory
`context["claim"]` the agents read.
- [orchestrator.py:52-55](backend/orchestrator.py#L52-L55) updates CSV only.
- [fraud_intelligence.py:81](backend/agents/fraud_intelligence.py#L81) and
  [:238](backend/agents/fraud_intelligence.py#L238) read `claim.get("claim_amount", 0)` → **always 0**.

**Impact:** these fraud flags can *never* trigger:
- "> ₹2,00,000 mandatory surveyor"
- "> ₹5,00,000 total-loss territory"
- "claim amount is X% of sum insured" (near-total-loss / inflation)

The Settlement LLM also receives `claim_amount: 0` in its claim context.

**Fix:** after writing the CSV, also update memory:
```python
context["claim"]["claim_amount"] = amount
```

### 2. Eligibility failures are flagged but not enforced
`context_verification` and `fraud_intelligence` both produce a *CRITICAL: policy
expired / pre-inception → NOT eligible* flag, but the **final decision is whatever
the LLM says** ([settlement_recommendation.py](backend/agents/settlement_recommendation.py) is pure prompt).
There is no deterministic guardrail, so an out-of-cover claim **can still be Approved**.

**Fix:** in the orchestrator, hard-force `decision = "Reject"` (or "Escalate") when an
eligibility failure is present, regardless of the LLM. Coverage is a binary fact, not a judgement call.

---

## 🟠 Settlement maths — doesn't match real motor claims

> All in [settlement_calc.py](backend/services/settlement_calc.py).

### 3. Claim amount stored as the **minimum** estimate
[orchestrator.py:54](backend/orchestrator.py#L54) uses `est.get("min")`. The repair band
is e.g. ₹37k–58k and we persist **₹37k**. Undervalues the claim everywhere `claim_amount`
is shown (queue, report, %-of-sum-insured). **Fix:** use the mid-point `(min+max)/2`.

### 4. No total-loss / IDV settlement path
Real rule (in our own KB: `total_loss_threshold: 75% of IDV`): if repair ≥ 75% of IDV,
the claim is a **total loss** settled at **IDV − salvage**, not repair cost. We never check
this. **Theft claims** (no photos → no damaged parts → repair = 0) currently produce a
**₹0 settlement** instead of an IDV payout. **Fix:** add a total-loss branch keyed on IDV/sum-insured.

### 5. Net payable never capped at Sum Insured / IDV
A large garage quote could produce a payout above the policy's coverage. Real payouts are
capped at IDV. **Fix:** `net = min(net, sum_insured)`.

### 6. Depreciation applied uniformly to all parts
We depreciate 70% of the whole repair at one age-based % ([`_depreciation_pct`](backend/services/settlement_calc.py#L23)).
Real IRDAI depreciation is **material-specific**:
- Glass: **0%** · Rubber/plastic/tyres/battery: **50%** · Fibreglass: **30%** · Metal: age slab.

So a windshield replacement is being wrongly depreciated. **Fix:** depreciate per damaged
part using its material/category (the pricing engine already knows the part list).

### 7. Zero-Depreciation add-on ignored
The settlement *prompt* says "apply depreciation if no Zero-Dep add-on", but the deterministic
engine always applies it, and `policies.csv` has **no add-on field**. Zero-Dep is one of the
most common Indian motor add-ons. **Fix:** add a `zero_dep` flag to policies; skip depreciation when true.

### 8. GST and deductibles are simplified
- GST is added **only on labour (18%)**. Real invoices carry GST on parts too (often 28%/18%).
  Net payable is understated.
- **Voluntary deductible** isn't captured (`policies.csv` has no field) — only the compulsory one.

### 9. Compulsory deductible hardcoded ₹1,000
Real: **₹1,000 for ≤1,500cc, ₹2,000 for >1,500cc**. Innova/Scorpio/BMW in the demo should be
₹2,000. No engine-cc data exists. **Fix:** add `engine_cc` to policies and branch.

### 14. NCB (No-Claim Bonus) not modelled
Approving a claim wipes the customer's NCB (20–50% renewal discount). Real insurers advise
"this ₹9k claim costs you ₹4k of NCB next year." `policies.csv` has no NCB% field even though
the CLAUDE.md demo table implies one. **Fix:** add NCB% + a small advisory (this was NEXTSTEPS #3).

---

## 🟠 Fraud & decision realism

### 10. Surveyor threshold contradicts itself
[fraud_intelligence.py:85](backend/agents/fraud_intelligence.py#L85) flags "> ₹2,00,000 →
mandatory surveyor (IRDAI)", but the KB (`mandatory_survey_above: 50000`) and the orchestrator
status logic both use **₹50,000**. Pick one (the ₹50k motor norm) and make it a shared constant.

### 11. The fraud **score** is LLM-generated, not deterministic
Rule flags are deterministic, but the final 0–100 `fraud_score` comes from the LLM
([fraud_intelligence.py](backend/agents/fraud_intelligence.py)), so the same claim can score
differently run-to-run. Regulators expect reproducible scoring. **Fix:** compute a deterministic
base score from the rule flags and let the LLM only *explain/adjust within a band*.

### 12. `traffic: "Moderate"` is fake data shown as real
[context_verification.py](backend/agents/context_verification.py) hardcodes traffic and surfaces
it in the summary as if it were a live signal. **Fix:** remove it, or clearly label it as
"not available / placeholder".

### 13. Third-party & theft claim types aren't really handled
The whole pipeline assumes **own-damage repair estimation**. A *Third Party* claim is about
injury/property damage to **others** (no own-vehicle repair); a *Theft* claim is a total loss
with no damage photos. Both flow through the damage→repair logic and produce odd results.
**Fix:** branch the pipeline by claim type (own-damage vs TP vs theft/total-loss).

---

## 🟡 Production-readiness gaps

### 15. Backend is unauthenticated
The new login is **frontend-only** (localStorage, spoofable). Every FastAPI route is open —
anyone can hit `/api/claims/.../investigate` or `/adjuster/decision` directly. Documented in
[login_implementation.md](login_implementation.md) §8; fine for the demo, must be fixed for prod
(role check dependency on sensitive routes).

### 16. CSV storage isn't concurrency-safe
[storage.update_claim_field](backend/storage.py) does read-all → modify → write-all. Two
investigations finishing together can clobber each other's rows. Also no PII protection, no
backups. **Fix (prod):** move to SQLite/Postgres.

### 17. Re-investigating a claim keeps the old human decision
`result.json` retains `adjuster_decision` / `adjuster_notes` across a fresh investigation, so a
stale human verdict can sit on top of brand-new AI findings. **Fix:** clear or version them on re-run.

### 18. SSE queues are never cleaned up
`_sse_queues` in [main.py:36](backend/main.py#L36) grows one entry per claim forever. Negligible
for a demo; a slow leak in long-running prod.

---

## Recommended quick wins (highest impact, lowest effort)

1. **#1** — one-line fix; instantly revives the amount-based fraud flags. *(5 min)*
2. **#2** — deterministic hard-reject on policy expiry; big credibility win. *(20 min)*
3. **#3** — use mid-point estimate for `claim_amount`. *(2 min)*
4. **#10** — unify the surveyor threshold to ₹50,000. *(5 min)*
5. **#12** — drop the fake "traffic: Moderate". *(2 min)*
6. **#5** — cap net payable at sum insured. *(5 min)*

Bigger but high-value for realism: **#4 total-loss/IDV path**, **#6 material-wise depreciation**,
**#7 zero-dep add-on**, **#13 claim-type branching**.

---

## What's already solid (no change needed)
- Deterministic pricing engine with segment fallback + live web + clamping.
- Settlement transparency / fair-value audit (inflation cap).
- Confidence-based routing + image-quality gate.
- Graceful RAG fallback; structured-JSON self-repair.
- Human-in-the-loop with audit trail; analytics tied to human approval.
