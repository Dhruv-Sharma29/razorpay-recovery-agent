# Failed-Payment & Subscription Recovery Agent — Implementation Plan v2
### Track 03: AI Revenue Recovery | Razorpay Hackathon | 4-Day Build
### Goal: 1st place, not a passing grade

---

## 0. What Changed From v1

- **Reasoning layer now runs on the Claude Agent SDK**, not a rules-engine string template. This mirrors Razorpay's own Agent Studio (built with Anthropic), so the demo is architecturally legible to any Razorpay/Anthropic judge in the room.
- **Every build item is tagged** 🟢 GOLDEN PATH (must work live, 100%, no exceptions) or 🟡 BATCH-ONLY (fine if it only ever shows up in the report, never demoed live). If you're behind on Day 3, you cut 🟡 items wholesale — never half-build three things.
- Pitch script, pre-empt questions, and backup-video plan are folded in as fixed checklist items, not optional polish.

---

## 1. One-Line Pitch

An agent that watches Razorpay test-mode payment failures, diagnoses *why* each one failed using a Claude Agent SDK reasoning loop, decides a bounded recovery action, executes it via the API, and shows a live, explainable audit trail of money recovered — with honest exception handling when it can't fix something.

**Opening line for the pitch (memorize, don't read off a slide):**
Quote Harshil Mathur from the FTX 2026 Agent Studio launch — paraphrase it as "businesses need systems that act, not just report" — then: "That's exactly what we built, on the same Claude Agent SDK Razorpay's own Agent Studio runs on." Straight into the live demo after that.

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
                 │  (rules + Claude Agent SDK     │  classification,
                 │   reasoning pass)              │  Claude explains
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
                 │  (Claude Agent SDK tool-use)    │
                 └─────────────────────────────┘
```

**Core principle to keep visible everywhere in the build:** every action must trace back to *(cause → rule fired → bounded limit → outcome)*. That traceability is the actual deliverable, not model accuracy — and not "we called an LLM," but "we called an LLM inside an auditable, capped, deterministic decision system."

**Where Claude Agent SDK actually sits (be precise about this in the pitch — judges will ask):**
1. Root-cause reasoning string generation — Claude reads the structured event + rule match and produces the human-readable "reasoning" field in the audit log, grounded in the rule that actually fired (not free-floating).
2. (Stretch, 🟡) NL query bar — a real tool-use loop over the audit log (Claude calls a `query_audit_log(filters)` tool), not a keyword filter dressed up as AI.

The rules engine still makes every bounded decision (retry / escalate / stop). Claude explains and queries; it never decides the action unsupervised. Say this explicitly on stage — it's the difference between "cool AI demo" and "would you trust this with real money," and it's the honest answer to the "why not just retry everything automatically" question.

---

## 3. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend / agent logic | Python (FastAPI) | fast to wire APIs + rules |
| Reasoning layer | **Claude Agent SDK** (`claude-sonnet-4-6` via the API) | matches Razorpay's own Agent Studio stack; produces the audit "why" string and (stretch) the NL query tool-use loop |
| Root-cause model | Rules-first, deterministic | explainable, testable, fast — this is the "gated" story, not model accuracy |
| Payments | Razorpay test-mode APIs (Orders, Payments, Subscriptions, Refunds) | required by the brief |
| Data store | SQLite or JSON append-only log | easy to demo-query |
| Frontend | React + TypeScript | polished live dashboard |
| Scheduling/retry | simple loop or task queue | don't over-engineer |

Keep the model layer intentionally small everywhere except the Claude Agent SDK integration — that's the one place extra polish pays off disproportionately.

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

Start with rules-based classification. Report rule-hit rate vs Claude-assisted explanation rate — don't claim the classifier itself is AI-driven if it isn't.

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
  "reasoning": "[Claude Agent SDK generated] Error matched insufficient-funds pattern; within retry limit (1/2); no amount cap triggered.",
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
- 🟢 Get a minimal Claude Agent SDK call working end-to-end (one hardcoded event → one reasoning string out). Do this Day 1, not Day 3 — it's your differentiator and the riskiest external dependency.

### Day 2 — Diagnosis + Decision Engine
- 🟢 Build the rules-based root-cause classifier (deterministic, explainable).
- 🟢 Wire the Claude Agent SDK reasoning call into the pipeline: rule fires → structured context passed to Claude → reasoning string returned → written to audit log.
- 🟢 Implement the decision policy engine: category + attempt history + amount → action + bound check + escalation check.
- 🟢 Unit-test the stopping rules directly — 5–10 explicit cases ("3rd insufficient-funds attempt must escalate, not retry"). Have these ready to show if asked "is this actually bounded or just described as bounded."
- 🟡 Small ML classifier layer for ambiguous cases — only if Day 2 finishes early.
- 🟢 Log every decision to the audit store, even before actions are wired to real APIs.

### Day 3 — Execution + Dashboard
- 🟢 Wire the Action Executor to real Razorpay test-mode calls for **one** flow end-to-end: insufficient-funds retry. This is the golden path — it must work live, every time.
- 🟢 Handle one deliberate failure gracefully in that same flow (API timeout / malformed response): catch it, log as exception with a clear reason, don't crash the loop.
- 🟡 Wire the remaining flows (mandate re-auth, gateway retry, card-switch, auth resend) — fine if these only ever run in batch, never live.
- 🟢 Build the React/TS dashboard: live audit-trail feed (cause → rule → action → outcome), running recovered-₹ counter, filter by escalated/exception.
- 🟡 NL query bar (Claude Agent SDK tool-use loop) — stretch, cut first if behind.
- 🔴 **Record the backup demo video tonight, no matter what.** Full golden-path run, screen-recorded. If live works on Day 4, don't mention it exists. If live stutters, pivot to it instantly.
- Checkpoint: review scope against time remaining. If behind, cut a whole 🟡 item — never half-build three.

### Day 4 — Batch Run, Metrics, Polish, Demo
- 🟢 Run the full batch (including the held-out slice) end-to-end.
- 🟢 Compute and display: recovery rate overall and by category, ₹ recovered, false-escalation cost estimate, exception list with reasons.
- 🟢 Write the README: problem, architecture diagram, decision policy table, metrics table, one audit-trail screenshot, the one gracefully-handled failure called out explicitly, and one line on the Claude Agent SDK integration.
- 🟢 Rehearse the 3-minute pitch (Section 6) until it's cold, especially the pre-empt answers.
- 🟢 Final checkpoint: if the golden path is solid and the backup video exists, stop touching code. Polish the pitch instead.

---

## 6. Pitch Script — 3 Minutes, Rehearsed Cold

- **0:00–0:15** — Quote (paraphrased) + one-line problem: revenue silently leaking through failed payments.
- **0:15–1:45** — Live demo: a failure comes in → diagnosis fires (Claude-generated reasoning visible) → bounded action executes → escalation case shown explicitly stopping instead of looping.
- **1:45–2:30** — Batch metrics screen: real numbers, real exception list, held-out slice called out as held-out.
- **2:30–3:00** — Close: "built the reasoning layer on the same Claude Agent SDK Razorpay's own Agent Studio runs on" + one sentence on what you'd add with more time.

**Pre-empt these questions cold — don't improvise them:**
- *"Why not just retry everything automatically?"* → amount-cap + stopping-rule answer, plus: rules decide, Claude only explains and (stretch) queries — never decides unsupervised.
- *"How does this generalize beyond synthetic data?"* → taxonomy is derived from real Razorpay error codes; decision policy is merchant-configurable; that's the extensibility story.
- *"What's your false-escalation cost?"* → have the actual computed number from the batch run, not a guess.

---

## 7. What Judges Will Actually Be Checking

- **Explainable:** every action has a visible "why" string, Claude-generated and rule-grounded — not just a category label.
- **Bounded:** hard caps and cooldowns enforced and demonstrably unit-tested, not just described.
- **Gated:** amount-threshold and unknown-cause cases always escalate rather than acting autonomously.
- **Audit trail:** append-only, timestamped, queryable/filterable in the dashboard.
- **One failure handled gracefully:** a real exception path shown live, not narrated.
- **Honest metrics:** reported on the held-out batch, includes false-escalation cost, no cherry-picking.
- **Built in the actual stack:** Claude Agent SDK for reasoning — the one feature most likely to be personally recognized by a Razorpay/Anthropic judge.

---

## 8. Stretch Ideas (only if Day 4 has slack — all 🟡)

- Hinglish notification copy for mandate re-auth prompts.
- A "promise-to-pay" flag when a customer manually confirms they'll pay later — tracked, not auto-retried.
- Natural-language query bar on the dashboard, real Claude Agent SDK tool-use loop over the audit log.

---

## 9. Execution Discipline

- Hard stop each night — a burnt-out builder ships worse code on Day 4.
- Review scope against time remaining every 12 hours.
- If behind by Day 3 morning, cut a whole 🟡 feature rather than under-build three.
- The golden path (insufficient-funds retry, live, 100% reliable) is the only thing that must never break. Everything else can degrade to "batch-report only" without costing you the win.
