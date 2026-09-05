# Failed-Payment & Subscription Recovery Agent — Implementation Plan

## 1. Goal and completion standard

Build a locally runnable AI-assisted revenue-recovery agent that detects revenue at risk, interprets failed Razorpay payment signals, recommends a bounded intervention, validates that recommendation through deterministic policy, safely executes or escalates it, and presents an append-only audit trail in the dashboard.

The repository is complete when all of the following are true:

- A new contributor can install dependencies and start the backend and frontend using documented commands.
- The insufficient-funds golden path completes end to end in Razorpay test mode or the safe mock executor.
- Model-generated reasoning appears when NIM responds; a useful policy-grounded fallback appears when it does not.
- The model can identify risk and recommend an intervention, but the deterministic policy gate remains the final authority over whether anything may execute.
- No frontend or model response can authorize, expand, or bypass a backend policy decision.
- Every policy rule, stop condition, escalation, and execution failure has automated test coverage.
- Synthetic and held-out evaluation results are reproducible and traceable to audit records.

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
                 │  AI Revenue-Risk Advisor     │  detects risk,
                 │  (Nemotron via NIM)          │  diagnoses signals,
                 │  untrusted recommendation    │  suggests intervention
                 └──────────┬───────────────────┘
                            │
                            ▼
                 ┌─────────────────────────────┐
                 │  Rules-First Classifier      │  validates the cause
                 │  (deterministic)             │  and supplies evidence
                 └──────────┬───────────────────┘
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
                 │  (optional NIM-assisted query)  │
                 └─────────────────────────────┘
```

**Core principle to keep visible everywhere in the build:** every action must trace back to *(cause → rule fired → bounded limit → outcome)*. That traceability is the actual deliverable, not model accuracy — and not "we called an LLM," but "we called an LLM inside an auditable, capped, deterministic decision system."

**Where the AI Model actually sits (be precise about this in the pitch — judges will ask):**
1. Revenue-risk detection — Nemotron identifies whether a failed payment or payment history represents recoverable revenue at risk, using only the structured event and approved history supplied to it.
2. Root-cause interpretation and intervention recommendation — Nemotron proposes a normalized cause and candidate action from a fixed allowlist. The recommendation is untrusted input to policy, not an authorization.
3. Explanation generation — Nemotron explains the final policy decision in operator- and customer-readable language.
4. (Stretch, BATCH-ONLY) NL query bar — only if implemented as a real model-assisted query over the audit log, not a keyword filter dressed up as AI.

The rules engine still makes every bounded decision (retry / escalate / stop). Nemotron detects and recommends; the policy engine validates, constrains, or rejects that recommendation, and the executor acts only on the final policy decision. Say this explicitly on stage — it is the honest answer to the "why not just retry everything automatically" question.

---

## 3. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend / agent logic | Python (FastAPI) | fast to wire APIs + rules |
| AI advisor / reasoning layer | **NVIDIA NIM API** (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`) | risk detection, cause interpretation, bounded intervention recommendation, and explanation; no recovery authority |
| Root-cause validator | Rules-first, deterministic | validates model signals, handles known causes, and fails closed on ambiguity |
| Payments | Razorpay test-mode APIs (Orders, Payments, Subscriptions, Refunds) | required by the brief |
| Data store | SQLite append-only log | easy to demo-query and verify |
| Frontend | React + TypeScript | polished live dashboard |
| Scheduling/retry | simple loop or task queue | don't over-engineer |

Keep the model layer intentionally small. The API request is optimized so the dashboard remains responsive; deterministic fallback risk/recommendation data and explanation text are shown if NIM times out or returns invalid output.

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

Keep classification rules-first and independently measurable. Report deterministic rule-hit rate, AI risk-detection quality, recommendation acceptance/constraining/rejection rates, and Nemotron explanation rate. Do not claim the classifier itself is AI-driven when the rules engine made the classification.

### 4.2A AI Recommendation (untrusted input to policy)

Nemotron receives the normalized event plus approved customer/payment history and returns a strictly validated recommendation. It must not receive or return credentials, raw payment secrets, arbitrary executable instructions, or an authority flag.

```json
{
  "revenue_at_risk": true,
  "risk_score": 0.91,
  "suggested_cause": "insufficient_funds",
  "suggested_action": "scheduled_retry",
  "confidence": 0.94,
  "evidence": [
    "Active subscription",
    "Insufficient-funds signal",
    "First failed attempt"
  ],
  "model_id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
}
```

The policy engine treats every field above as untrusted. It independently checks the event, deterministic classification, attempt history, amount cap, cooldown, mandate state, merchant configuration, and action allowlist. The final `PolicyDecision` is the only object that can authorize execution.

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
  "revenue_at_risk": true,
  "risk_score": 0.91,
  "ai_suggested_cause": "insufficient_funds",
  "ai_suggested_action": "scheduled_retry",
  "ai_confidence": 0.94,
  "recommendation_status": "accepted | constrained | rejected | unavailable",
  "rule_fired": "policy.insufficient_funds.retry_24h",
  "policy_action": "scheduled_retry",
  "scheduled_for": "2026-09-01T10:00:00Z",
  "attempt_number": 1,
  "outcome": "pending | recovered | failed | escalated",
  "amount_recovered": 149900,
  "reasoning": "[Nemotron-generated or policy-grounded fallback] Error matched insufficient-funds pattern; within retry limit (1/2); no amount cap triggered.",
  "timestamp": "2026-08-31T10:00:05Z"
}
```

---

## 5. Task-Based Implementation Plan (tagged GOLDEN PATH / BATCH-ONLY)

### T01 — Repository and configuration
- Confirm the FastAPI, React/TypeScript, SQLite audit, NIM API, and Razorpay test-mode boundaries.
- Define configurable NIM API Key/URL/model, reasoning timeout, amount cap, database path, and frontend API base URL.
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
- **Complete when:** classification is deterministic, explainable, and independent of Nemotron.

### T05 — Bounded policy engine
- Implement the decision table for retry, re-authorization, channel switch, notification, stop, and escalation.
- Extend the policy input contract to accept an optional AI recommendation as an untrusted candidate; define the deterministic action allowlist and the outcomes `accepted`, `constrained`, `rejected`, and `unavailable`.
- Enforce retry limits, cooldowns, the global three-attempt cap, and the configurable amount cap.
- Ensure unknown failures, missing decisions, exhausted limits, and high-value events cannot auto-recover.
- Add explicit stopping-rule tests, including third insufficient-funds attempt escalation.
- **Complete when:** only the policy engine can authorize recovery and all limits are unit-tested.

### T06 — AI revenue-risk detection and intervention recommendation
- Add `RecoveryRecommendation` as a separate, untrusted data model containing `revenue_at_risk`, `risk_score`, `suggested_cause`, `suggested_action`, `confidence`, evidence, model ID, prompt version, and latency.
- Add a `RecoveryRecommender` module that sends only normalized payment data and approved history to Nemotron through NVIDIA NIM.
- Constrain model output to the known failure taxonomy and a fixed action allowlist; reject malformed JSON, unknown actions, unsupported causes, unsafe text, and out-of-range scores.
- Keep the existing `FailureClassifier` as an independent deterministic validator. Do not replace it with the model.
- Return a deterministic recommendation fallback when NIM is unavailable: use the classifier's cause, mark `revenue_at_risk` according to explicit policy inputs, and set `suggested_action` to `None` when confidence is insufficient.
- **Complete when:** a model recommendation is available for a valid event, every malformed/unavailable path fails safely, and no recommendation can execute an action directly.

### T07 — NIM advisory reasoning
- Wire the Python backend to `integrate.api.nvidia.com/v1/chat/completions`.
- Create a strict explanation prompt bounded by the deterministic classification and final policy result. Keep recommendation and explanation prompts logically separate, even if they share one NIM request later for latency.
- Produce structured JSON explanations.
- On timeout, unavailable API, or malformed output, show policy-grounded fallback reasoning without changing policy authority.
- **Complete when:** successful calls show `reasoning_success=true`; failures show a useful fallback and never authorize or change an action.

### T08 — AI-assisted end-to-end recovery pipeline
- Orchestrate ingestion → AI risk recommendation → deterministic classification → policy validation/constraining → advisory explanation → execution/escalation → audit.
- Pass the AI recommendation into policy as a candidate only; never copy its action directly into `PolicyDecision`.
- Make the policy engine record whether the recommendation was accepted, constrained, rejected, or unavailable, with a deterministic reason.
- Preserve each component result and never allow Nemotron to mutate policy or execution.
- Test accepted recommendation, constrained recommendation, rejected recommendation, low-confidence recommendation, NIM failure, denial, escalation, executor failure, reasoning failure, and audit failure paths.
- **Complete when:** the golden path visibly shows AI recommendation → policy decision → bounded execution, and all safety stop paths produce inspectable results.

### T09 — Action execution and escalation
- Wire the insufficient-funds scheduled retry to Razorpay test mode or the safe mock executor.
- Catch downstream timeouts and malformed responses; return a failed result instead of crashing.
- BATCH-ONLY: Add mandate re-authorization, gateway retry, card switching, and authentication resend only after the golden path is stable.
- Escalate every denied, unknown, over-cap, exhausted, or failed case with a human-readable reason.
- **Complete when:** every automated action is bounded and every stop/escalation is recorded.

### T10 — Append-only audit log
- Persist classification, policy, reasoning status/reference, execution, escalation, outcome, amount, attempt, and errors.
- Persist the AI recommendation separately from the final policy decision, including risk score, suggested action, confidence, acceptance/constraining status, and fallback reason.
- Keep the log append-only and redact credentials or sensitive values before persistence.
- Expose read-only audit retrieval for the dashboard.
- **Complete when:** each pipeline run has an auditable cause → rule → action → outcome trail.

### T11 — React dashboard
- Render the live outcome, classification, policy decision, reasoning recommendation/explanation, execution, escalation, and audit trail.
- Show “AI suggested” and “Policy authorized” as separate fields. Make constrained or rejected recommendations visible rather than hiding them.
- Show revenue at risk, risk score, and recovered amount as separate metrics; never claim that the AI recovered money directly.
- Clearly distinguish Nemotron-generated reasoning from policy-grounded fallback reasoning.
- Show recovered amount/counts and filters for escalated, failed, or exceptional outcomes.
- Keep all authorization decisions in the backend; the frontend remains display-only.
- **Complete when:** Safari/Chrome can submit the golden-path event and visibly show the full pipeline.

### T12 — Evaluation and metrics
- Run synthetic and held-out data end-to-end.
- Report revenue-risk detection precision/recall, recommendation acceptance/constraining/rejection rates, recovery rate overall and by category, recovered amount, false-escalation cost, and exception reasons.
- Include a policy-isolation metric: AI recommendations must never increase the number of authorized actions beyond deterministic policy.
- Verify held-out results are reported separately and reproducibly.
- **Complete when:** metrics are generated from commands and can be traced back to audit records.

### T13 — Documentation and demo readiness
- Document setup, environment variables, architecture, policy table, safety boundary, fallback behavior, and test commands.
- Capture a successful golden-path demo, one recommendation-constrained path, and one graceful NIM failure path.
- Prepare the pitch around AI-assisted detection and recommendation behind a deterministic policy gate.
- **Complete when:** a new contributor can run, test, understand, and demo the complete repository.

### Task dependency order

Complete tasks in this order: `T01 → T02 → T03 → T04 → T05 → T06 → T07 → T08 → T09/T10 → T11 → T12 → T13`.

`T09` and `T10` may proceed in parallel after `T08`, but `T11` depends on their response shapes. Do not begin BATCH-ONLY work until `T01`–`T11` are complete and the golden path is stable.

### Verification commands

Run the following checks before declaring the repository complete:

```bash
# Backend unit and integration tests
cd backend
python -m pytest -q

# Frontend tests and production build
cd ../frontend
npm test -- --run
npm run build
```

For the live verification, provide the NIM API key, start the FastAPI backend on port `8000`, start Vite on port `5173`, submit an insufficient-funds event, and verify an AI recommendation, a separate final policy decision, `Reasoning: Generated`, a recovered or safely bounded outcome, and a new audit record. Provide an invalid API key only for the explicit fallback-path check.

---

## 6. Pitch Script — 3 Minutes, Rehearsed Cold

- **0:00–0:15** — Quote (paraphrased) + one-line problem: revenue silently leaking through failed payments.
- **0:15–1:45** — Live demo: a failure comes in → AI detects revenue at risk and recommends an intervention → deterministic policy accepts or constrains it → bounded action executes → escalation case shown explicitly stopping instead of looping.
- **1:45–2:30** — Batch metrics screen: real numbers, real exception list, held-out slice called out as held-out.
- **2:30–3:00** — Close: explain that NIM detects and recommends, while deterministic policy controls recovery, limits, and stopping + one sentence on what you'd add with more time.

**Pre-empt these questions cold — don't improvise them:**
- *"Why not just retry everything automatically?"* → amount-cap + stopping-rule answer, plus: Nemotron can recommend, but policy validates every recommendation and can constrain or reject it.
- *"How does this generalize beyond synthetic data?"* → taxonomy is derived from real Razorpay error codes; decision policy is merchant-configurable; that's the extensibility story.
- *"What's your false-escalation cost?"* → have the actual computed number from the batch run, not a guess.

---

## 7. What Judges Will Actually Be Checking

- **AI-assisted:** the model detects risk, diagnoses signals, and recommends an intervention; the dashboard shows that recommendation separately from the final policy decision.
- **Explainable:** every action has a visible "why" string, Nemotron-generated when available and policy-grounded on fallback — not just a category label.
- **Bounded:** hard caps and cooldowns enforced and demonstrably unit-tested, not just described.
- **Gated:** amount-threshold and unknown-cause cases always escalate rather than acting autonomously.
- **Audit trail:** append-only, timestamped, queryable/filterable in the dashboard.
- **One failure handled gracefully:** a real exception path shown live, not narrated.
- **Honest metrics:** reported on the held-out batch, includes false-escalation cost, no cherry-picking.
- **Built in the actual stack:** NVIDIA NIM for risk/recommendation/reasoning, with a deterministic policy boundary.

---

## 8. Stretch Ideas (only after all golden-path tasks are complete — all BATCH-ONLY)

- Hinglish notification copy for mandate re-auth prompts.
- A "promise-to-pay" flag when a customer manually confirms they'll pay later — tracked, not auto-retried.
- Natural-language query bar on the dashboard, if a bounded NIM-assisted query loop is added.

---

## 9. Execution Discipline

- Complete tasks in dependency order and keep the golden path runnable after each integration.
- Validate every safety boundary with a test before adding the next layer.
- If scope is constrained, cut a whole BATCH-ONLY feature rather than under-building several features.
- The golden path (insufficient-funds retry, live, 100% reliable) is the only thing that must never break. Everything else can degrade to "batch-report only" without costing you the win.
