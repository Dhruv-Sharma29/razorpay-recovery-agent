# Failed-Payment & Subscription Recovery Agent — Implementation Plan v2
### Track 03: AI Revenue Recovery | Razorpay Hackathon | 4-Day Build
### Goal: 1st place, not a passing grade

---

## 0. What Changed From v1

- **Reasoning layer runs on Qwen via Ollama**, not a rules-engine string template. Qwen receives structured event, classification, and policy context and returns an explanation only.
- **Every build item is tagged** 🟢 GOLDEN PATH (must work live, 100%, no exceptions) or 🟡 BATCH-ONLY (fine if it only ever shows up in the report, never demoed live). If you're behind on Day 3, you cut 🟡 items wholesale — never half-build three things.
- Pitch script, pre-empt questions, and backup-video plan are folded in as fixed checklist items, not optional polish.

---

## 1. One-Line Pitch

An agent that watches Razorpay test-mode payment failures, diagnoses *why* each one failed using a Qwen/Ollama reasoning pass, decides a bounded recovery action, executes it via the API, and shows a live, explainable audit trail of money recovered — with honest exception handling when it can't fix something.

**Opening line for the pitch (memorize, don't read off a slide):**
Quote Harshil Mathur from the FTX 2026 Agent Studio launch — paraphrase it as "businesses need systems that act, not just report" — then introduce the bounded Qwen/Ollama reasoning layer. Straight into the live demo after that.

---

## 2. System Architecture

```
                 ┌─────────────────────┐
  synthetic /    │   Ingestion Layer    │   normalizes failed
  Razorpay       │  (webhook / batch    │   payment events into
  test events ──▶│   loader)            │   a common schema
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌─────────────────────────────┐
                 │  Root-Cause Engine            │  rules-first
                 │  (rules + Qwen/Ollama           │  classification,
                 │   reasoning pass)              │  Qwen explains
                 └──────────┬───────────────────┘  the "why"
                            │
                            ▼
                 ┌─────────────────────┐
                 │  Decision Policy     │  category + retry
                 │  Engine (bounded,    │  history → action +
                 │  gated rules table)  │  stop/escalate check
                 └──────────┬───────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
   ┌─────────────────────┐     ┌─────────────────────┐
   │  Action Executor     │     │  Escalation Handler  │
   │  (Razorpay test-mode │     │  (flag for human,    │
   │   API calls)         │     │   no further action) │
   └──────────┬───────────┘     └──────────┬───────────┘
              │                            │
              └─────────────┬──────────────┘
                            ▼
                 ┌─────────────────────┐
                 │   Audit Log Store    │  every decision +
                 │   (append-only)      │  action + outcome,
                 └──────────┬───────────┘  timestamped
                            │
                            ▼
                 ┌─────────────────────────────┐
                 │  Dashboard (React)            │  live feed + batch
                 │  audit trail + NL query bar    │  report + metrics
                 │  (optional Qwen/Ollama query)   │
                 └─────────────────────────────┘
```

**Core principle to keep visible everywhere in the build:** every action must trace back to *(cause → rule fired → bounded limit → outcome)*. That traceability is the actual deliverable, not model accuracy — and not "we called an LLM," but "we called an LLM inside an auditable, capped, deterministic decision system."

**Where Qwen/Ollama actually sits (be precise about this in the pitch — judges will ask):**
1. Root-cause reasoning string generation — Qwen reads the structured event + rule match and produces the human-readable reasoning field, grounded in the rule that actually fired.
2. (Stretch, 🟡) NL query bar — only if implemented as a real model-assisted query over the audit log, not a keyword filter dressed up as AI.

The rules engine still makes every bounded decision (retry / escalate / stop). Qwen explains; it never decides the action unsupervised. Say this explicitly on stage — it's the honest answer to the "why not just retry everything automatically" question.

---

## 3. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend / agent logic | Python (FastAPI) | fast to wire APIs + rules |
| Reasoning layer | **Qwen via Ollama** (`qwen3.5:latest`) | local structured explanation of the audit "why" string; no recovery authority |
| Root-cause model | Rules-first, deterministic | explainable, testable, fast — this is the "gated" story, not model accuracy |
| Payments | Razorpay test-mode APIs (Orders, Payments, Subscriptions, Refunds) | required by the brief |
| Data store | SQLite or JSON append-only log | easy to demo-query |
| Frontend | React + TypeScript | polished live dashboard |
| Scheduling/retry | simple loop or task queue | don't over-engineer |

Keep the model layer intentionally small. The Ollama request disables extended thinking and caps output at 128 tokens so the dashboard remains responsive; deterministic fallback text is shown if Ollama times out or returns invalid output.

---

## 4. Data Model

### 4.1 Failed Transaction Event (input)
```json
{
  "event_id": "evt_001",
  "razorpay_payment_id": "pay_test_xxx",
  "merchant_id": "merch_01",
  "customer_id": "cust_001",
  "type": "subscription | one_time",
  "amount": 149900,
  "currency": "INR",
  "payment_method": "upi | card | netbanking",
  "error_code": "BAD_REQUEST_ERROR | GATEWAY_ERROR | ...",
  "error_description": "insufficient funds",
  "attempt_number": 1,
  "mandate_status": "active | expired | paused | null",
  "timestamp": "2026-08-31T10:00:00Z"
}
```

### 4.2 Failure Taxonomy → Root Cause Categories

| Category | Signal(s) | Typical Razorpay error |
|---|---|---|
| Insufficient funds | error_description match, repeat within short window | `INSUFFICIENT_FUNDS` |
| Expired/inactive mandate | mandate_status = expired/paused | `MANDATE_EXPIRED` |
| Bank/gateway timeout | error_code = GATEWAY_ERROR, single occurrence | `GATEWAY_ERROR` |
| Card declined (issuer) | error_code = CARD_DECLINED | `CARD_DECLINED` |
| Auth/OTP failure | error_description mentions OTP/3DS | `AUTHENTICATION_ERROR` |
| Unknown/ambiguous | doesn't cleanly match rules | — |

Start with rules-based classification. Report rule-hit rate vs Qwen-assisted explanation rate — don't claim the classifier itself is AI-driven if it isn't.

### 4.3 Decision Policy Table (build this Day 1 — it's the differentiator)

| Root Cause | Action | Bound / Limit | Escalation Trigger |
|---|---|---|---|
| Insufficient funds | Retry after 24h | max 2 retries | 3rd failure → escalate, stop |
| Expired/paused mandate | Trigger re-authorization request | 1 attempt, no auto-retry after | no response in 72h → escalate |
| Bank/gateway timeout | Immediate retry once | 1 immediate retry | fails again → switch channel once, then escalate |
| Card declined | Switch to alternate saved method if available, else notify | 1 switch attempt | no alt method → escalate immediately |
| Auth/OTP failure | Resend auth prompt | max 1 resend | fails again → escalate |
| Unknown | No auto action | 0 | always escalate |

**Global stopping rules (state explicitly in the demo):**
- Hard cap: no more than 3 total automated attempts per transaction, ever.
- Cooldown enforced between retries per policy above.
- Amount cap: auto-recovery disabled above a configurable ₹ threshold — high-value failures always escalate to human. Strongest single "bounded and gated" talking point — lead with it if asked about safety.
- Every escalation and every stop is logged with a reason string, never silently dropped.

### 4.4 Audit Log Entry (output — powers the dashboard)
```json
{
  "event_id": "evt_001",
  "diagnosed_cause": "insufficient_funds",
  "confidence": 0.91,
  "rule_fired": "R1_retry_24h",
  "action_taken": "scheduled_retry",
  "scheduled_for": "2026-09-01T10:00:00Z",
  "attempt_number": 1,
  "outcome": "pending | recovered | failed | escalated",
  "amount_recovered": 149900,
  "reasoning": "[Qwen-generated or policy-grounded fallback] Error matched insufficient-funds pattern; within retry limit (1/2); no amount cap triggered.",
  "timestamp": "2026-08-31T10:00:05Z"
}
```

---

## 5. Day-by-Day Plan (tagged 🟢 golden path / 🟡 batch-only)

### Day 1 — Foundation
- 🟢 Set up Razorpay test-mode account, generate API keys, manually confirm Orders/Payments/Subscriptions/Refunds request/response shapes.
- 🟢 Write synthetic dataset generator: 50–100 failed transactions across the 5–6 categories, mixed subscription/one-time, some multi-attempt histories.
- 🟢 Freeze a held-out ~20% slice — untouched until Day 4, this is what makes the metrics honest.
- 🟢 Finalize failure taxonomy + decision policy table as an actual doc/config, not implicit in code.
- 🟢 Get a minimal Qwen/Ollama call working end-to-end (one hardcoded event → one reasoning string out). Keep the policy-grounded fallback available when the model is unavailable.

### Day 2 — Diagnosis + Decision Engine
- 🟢 Build the rules-based root-cause classifier (deterministic, explainable).
- 🟢 Wire the Qwen/Ollama reasoning call into the pipeline: rule fires → structured context passed to Qwen → reasoning string returned → written to audit log.
- 🟢 Implement the decision policy engine: category + attempt history + amount → action + bound check + escalation check.
- 🟢 Unit-test the stopping rules directly — 5–10 explicit cases ("3rd insufficient-funds attempt must escalate, not retry"). Have these ready to show if asked "is this actually bounded or just described as bounded."
- 🟡 Small ML classifier layer for ambiguous cases — only if Day 2 finishes early.
- 🟢 Log every decision to the audit store, even before actions are wired to real APIs.

### Day 3 — Execution + Dashboard
- 🟢 Wire the Action Executor to real Razorpay test-mode calls for **one** flow end-to-end: insufficient-funds retry. This is the golden path — it must work live, every time.
- 🟢 Handle one deliberate failure gracefully in that same flow (API timeout / malformed response): catch it, log as exception with a clear reason, don't crash the loop.
- 🟡 Wire the remaining flows (mandate re-auth, gateway retry, card-switch, auth resend) — fine if these only ever run in batch, never live.
- 🟢 Build the React/TS dashboard: live audit-trail feed (cause → rule → action → outcome), running recovered-₹ counter, filter by escalated/exception.
- 🟡 NL query bar (Qwen/Ollama-assisted query) — stretch, cut first if behind.
- 🔴 **Record the backup demo video tonight, no matter what.** Full golden-path run, screen-recorded. If live works on Day 4, don't mention it exists. If live stutters, pivot to it instantly.
- Checkpoint: review scope against time remaining. If behind, cut a whole 🟡 item — never half-build three.

### Day 4 — Batch Run, Metrics, Polish, Demo
- 🟢 Run the full batch (including the held-out slice) end-to-end.
- 🟢 Compute and display: recovery rate overall and by category, ₹ recovered, false-escalation cost estimate, exception list with reasons.
- 🟢 Write the README: problem, architecture diagram, decision policy table, metrics table, one audit-trail screenshot, the one gracefully-handled failure called out explicitly, and one line on the Qwen/Ollama integration.
- 🟢 Rehearse the 3-minute pitch (Section 6) until it's cold, especially the pre-empt answers.
- 🟢 Final checkpoint: if the golden path is solid and the backup video exists, stop touching code. Polish the pitch instead.

---

## 6. Pitch Script — 3 Minutes, Rehearsed Cold

- **0:00–0:15** — Quote (paraphrased) + one-line problem: revenue silently leaking through failed payments.
- **0:15–1:45** — Live demo: a failure comes in → diagnosis fires (Qwen-generated reasoning visible) → bounded action executes → escalation case shown explicitly stopping instead of looping.
- **1:45–2:30** — Batch metrics screen: real numbers, real exception list, held-out slice called out as held-out.
- **2:30–3:00** — Close: explain that Qwen/Ollama provides advisory reasoning while deterministic policy controls recovery + one sentence on what you'd add with more time.

**Pre-empt these questions cold — don't improvise them:**
- *"Why not just retry everything automatically?"* → amount-cap + stopping-rule answer, plus: rules decide, Qwen only explains — never decides unsupervised.
- *"How does this generalize beyond synthetic data?"* → taxonomy is derived from real Razorpay error codes; decision policy is merchant-configurable; that's the extensibility story.
- *"What's your false-escalation cost?"* → have the actual computed number from the batch run, not a guess.

---

## 7. What Judges Will Actually Be Checking

- **Explainable:** every action has a visible "why" string, Qwen-generated when available and policy-grounded on fallback — not just a category label.
- **Bounded:** hard caps and cooldowns enforced and demonstrably unit-tested, not just described.
- **Gated:** amount-threshold and unknown-cause cases always escalate rather than acting autonomously.
- **Audit trail:** append-only, timestamped, queryable/filterable in the dashboard.
- **One failure handled gracefully:** a real exception path shown live, not narrated.
- **Honest metrics:** reported on the held-out batch, includes false-escalation cost, no cherry-picking.
- **Built in the actual stack:** Qwen via Ollama for reasoning, with a deterministic policy boundary.

---

## 8. Stretch Ideas (only if Day 4 has slack — all 🟡)

- Hinglish notification copy for mandate re-auth prompts.
- A "promise-to-pay" flag when a customer manually confirms they'll pay later — tracked, not auto-retried.
- Natural-language query bar on the dashboard, if a bounded Qwen/Ollama-assisted query loop is added.

---

## 9. Execution Discipline

- Hard stop each night — a burnt-out builder ships worse code on Day 4.
- Review scope against time remaining every 12 hours.
- If behind by Day 3 morning, cut a whole 🟡 feature rather than under-build three.
- The golden path (insufficient-funds retry, live, 100% reliable) is the only thing that must never break. Everything else can degrade to "batch-report only" without costing you the win.
