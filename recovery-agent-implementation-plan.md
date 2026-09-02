# Failed-Payment & Subscription Recovery Agent — Implementation Plan v2
### Track 03: AI Revenue Recovery | Razorpay Hackathon | Complete Build Plan
### Goal: 1st place, not a passing grade

---

## 0. What Changed From v1

- **Reasoning layer runs on Qwen via Ollama**, not a rules-engine string template. Qwen receives structured event, classification, and policy context and returns an explanation only.
- **Every build item is tagged** GOLDEN PATH (must work live, 100%, no exceptions) or BATCH-ONLY (fine if it only ever shows up in the report, never demoed live). If scope is constrained, cut BATCH-ONLY items wholesale — never half-build three things.
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
2. (Stretch, BATCH-ONLY) NL query bar — only if implemented as a real model-assisted query over the audit log, not a keyword filter dressed up as AI.

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

### 4.3 Decision Policy Table (the differentiator)

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

## 5. Task-Based Implementation Plan (tagged GOLDEN PATH / BATCH-ONLY)

### T01 — Repository and configuration
- Confirm the FastAPI, React/TypeScript, SQLite audit, Ollama, and Razorpay test-mode boundaries.
- Keep credentials in environment variables only; never commit secrets.
- Define configurable Ollama URL/model, reasoning timeout, amount cap, database path, and frontend API base URL.
- **Complete when:** a clean checkout can install dependencies and start both services with documented commands.

### T02 — Event ingestion and validation
- Normalize webhook, synthetic, and manually submitted failures into `FailedTransactionEvent`.
- Validate IDs, amount, currency, payment method, failure fields, attempt number, and timestamp.
- Reject malformed events before classification or execution.
- **Complete when:** valid events reach the pipeline and invalid events return safe validation errors.

### T03 — Synthetic and held-out data
- Generate representative failures for insufficient funds, mandate state, gateway timeout, card decline, authentication failure, and unknown failures.
- Include one-time and subscription events plus multi-attempt histories.
- Freeze a held-out slice and prevent evaluation code from training on or mutating it.
- **Complete when:** synthetic and held-out datasets load through the same ingestion path.

### T04 — Deterministic failure classification
- Implement rules-first classification with category, confidence, rule ID, source field, and reason.
- Make unknown or ambiguous failures explicit; do not infer an auto-recovery category.
- Add unit tests for every rule, precedence case, and unknown path.
- **Complete when:** classification is deterministic, explainable, and independent of Qwen.

### T05 — Bounded policy engine
- Implement the decision table for retry, re-authorization, channel switch, notification, stop, and escalation.
- Enforce retry limits, cooldowns, the global three-attempt cap, and the configurable amount cap.
- Ensure unknown failures, missing decisions, exhausted limits, and high-value events cannot auto-recover.
- Add explicit stopping-rule tests, including third insufficient-funds attempt escalation.
- **Complete when:** only the policy engine can authorize recovery and all limits are unit-tested.

### T06 — Qwen/Ollama advisory reasoning
- Send structured event, classification, and policy context to Qwen using JSON output.
- Set `think: false` and cap output with `num_predict: 128` so the dashboard remains responsive.
- Parse and validate recommendation, explanation, and confidence.
- On timeout, unavailable Ollama, or malformed output, show policy-grounded fallback reasoning without changing policy authority.
- **Complete when:** successful calls show `reasoning_success=true`; failures show a useful fallback and never authorize an action.

### T07 — End-to-end recovery pipeline
- Orchestrate ingestion → classification → policy → reasoning → execution/escalation → audit.
- Preserve each component result and never allow Qwen to mutate policy or execution.
- Test success, denial, escalation, executor failure, reasoning failure, and audit failure paths.
- **Complete when:** the golden path and all safety stop paths produce inspectable pipeline results.

### T08 — Action execution and escalation
- Wire the insufficient-funds scheduled retry to Razorpay test mode or the safe mock executor.
- Catch downstream timeouts and malformed responses; return a failed result instead of crashing.
- BATCH-ONLY: Add mandate re-authorization, gateway retry, card switching, and authentication resend only after the golden path is stable.
- Escalate every denied, unknown, over-cap, exhausted, or failed case with a human-readable reason.
- **Complete when:** every automated action is bounded and every stop/escalation is recorded.

### T09 — Append-only audit log
- Persist classification, policy, reasoning status/reference, execution, escalation, outcome, amount, attempt, and errors.
- Keep the log append-only and redact credentials or sensitive values before persistence.
- Expose read-only audit retrieval for the dashboard.
- **Complete when:** each pipeline run has an auditable cause → rule → action → outcome trail.

### T10 — React dashboard
- Render the live outcome, classification, policy decision, reasoning recommendation/explanation, execution, escalation, and audit trail.
- Clearly distinguish Qwen-generated reasoning from policy-grounded fallback reasoning.
- Show recovered amount/counts and filters for escalated, failed, or exceptional outcomes.
- Keep all authorization decisions in the backend; the frontend remains display-only.
- **Complete when:** Safari/Chrome can submit the golden-path event and visibly show the full pipeline.

### T11 — Evaluation and metrics
- Run synthetic and held-out data end-to-end.
- Report recovery rate overall and by category, recovered amount, false-escalation cost, and exception reasons.
- Verify held-out results are reported separately and reproducibly.
- **Complete when:** metrics are generated from commands and can be traced back to audit records.

### T12 — Documentation and demo readiness
- Document setup, environment variables, architecture, policy table, safety boundary, fallback behavior, and test commands.
- Capture a successful golden-path demo and one graceful failure path.
- Prepare the pitch around bounded deterministic decisions with advisory Qwen explanations.
- **Complete when:** a new contributor can run, test, understand, and demo the complete repository.

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

## 8. Stretch Ideas (only after all golden-path tasks are complete — all BATCH-ONLY)

- Hinglish notification copy for mandate re-auth prompts.
- A "promise-to-pay" flag when a customer manually confirms they'll pay later — tracked, not auto-retried.
- Natural-language query bar on the dashboard, if a bounded Qwen/Ollama-assisted query loop is added.

---

## 9. Execution Discipline

- Complete tasks in dependency order and keep the golden path runnable after each integration.
- Validate every safety boundary with a test before adding the next layer.
- If scope is constrained, cut a whole BATCH-ONLY feature rather than under-building several features.
- The golden path (insufficient-funds retry, live, 100% reliable) is the only thing that must never break. Everything else can degrade to "batch-report only" without costing you the win.
