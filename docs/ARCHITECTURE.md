# Architecture

## The one rule

**The policy engine is the sole authority on whether money moves.** Everything
else either supplies evidence to it or carries out what it decided.

The model never classifies, never authorises, never sets a limit, and never
executes. It has one bounded power, described below.

---

## Request path

```
FailedTransactionEvent
        │
   1. Classifier          deterministic rules on the error code.
        │                 The event's self-reported failure_category is
        │                 never trusted as input.
        │
   2. Recommender  (NIM)  advisory. Sees the classification, redacted
        │                 customer history, the permitted-action menu, and
        │                 measured outcomes. Returns a suggestion.
        │
   3. Policy engine       decides. Amount cap, retry limits, global attempt
        │                 cap, cooldown. Publishes permitted_actions.
        │
   3b. Bounded choice     the advisor's suggestion becomes the action ONLY if
        │                 it is already inside permitted_actions, recovery is
        │                 authorised, and confidence ≥ threshold.
        │
   4b. Bounded timing     the advisor may move the cooldown inside a published
        │                 window (0.5×–3× the default). Outside it is
        │                 discarded, never clamped.
        │
   5. Executor            only policy-authorised actions. Durable idempotency
        │                 ledger. Deferred retries are scheduled, not executed.
        │
   6. Outreach            customer contact, only for actions that warrant it.
        │                 A silent rail retry is not contact.
        │
   7. Audit               append-only, hash-chained. Written last, always.
```

`action_source` and `delay_source` record whether policy or the model chose,
so the audit trail says who decided what.

---

## Modules

| Path | Responsibility |
|---|---|
| `app/classifier/` | Error code → failure category. Deterministic. |
| `app/recommendation/` | NIM advisory: cause, action, risk, retry timing. |
| `app/policy/` | The authority. Rules, caps, permitted actions, cooldown windows. |
| `app/pipeline/` | Orchestration and the bounded-choice gates. |
| `app/executor/` | Simulated and real execution behind one contract. |
| `app/razorpay/` | Live sandbox executor, webhook ingestion, credential check. |
| `app/outreach/` | Customer contact dispatch. |
| `app/scheduler/` | Deferred retries whose cooldown has elapsed. |
| `app/persistence/` | Idempotency ledger, attempt history, scheduled jobs. |
| `app/audit/` | Append-only hash-chained log, plus outcome aggregation. |
| `app/evaluation/` | Harness: funnel stages, policy-isolation checks. |
| `app/ingestion/` | Seeded synthetic event generation. |

---

## Policy rules

| Cause | Action | Retries | Cooldown | Alternatives |
|---|---|---|---|---|
| `insufficient_funds` | `scheduled_retry` | 2 | 24h | — |
| `expired_card` | `trigger_reauthorization` | 1 | — | — |
| `network_error` | `immediate_retry` | 1 | — | `scheduled_retry` |
| `bank_decline` | `switch_payment_method` | 1 | — | `scheduled_retry` |
| `authentication_failure` | `resend_auth_prompt` | 1 | — | `scheduled_retry` |
| `overdue_receivable` | `send_payment_reminder` | 3 | 72h | — |
| `unknown` | `no_action` | 0 | — | — |

Only three causes offer the advisor a genuine choice. Everywhere else policy is
decisive and there is nothing to decide — which is why the A/B reports its
opportunity ceiling alongside its result.

Above these sit a global attempt cap and an amount limit
(`AUTO_RECOVERY_AMOUNT_LIMIT`, default ₹5,000). Anything above the cap is
escalated for manual handling and never retried automatically.

---

## Safety properties, and what enforces each

| Property | Enforced by |
|---|---|
| The model cannot authorise recovery | `automatic_recovery_allowed` is set by policy only; step 3b cannot touch it |
| The model cannot invent an action | Suggestion must be in `permitted_actions` |
| An unsure model cannot override | `MODEL_ACTION_CHOICE_MIN_CONFIDENCE`, default 0.7 |
| A payment is never executed twice | SHA-256 idempotency key, in-memory cache plus durable ledger |
| High-value payments are never auto-retried | Amount cap in policy, **re-checked independently in the executor** |
| Untrusted input is never believed | The event's own `failure_category` is ignored; the classifier decides |
| The audit trail cannot be quietly edited | Hash chain: each record stores the previous record's hash |
| Audit writes cannot be reversed | The audit module performs `INSERT` only — it has no update or delete path |
| A missing integration is not a decline | Executor returns `not_attempted`, distinct from `failed` |

---

## Persistence

Raw `sqlite3`, no ORM.

- `audit_log` — append-only. `previous_hash` + `record_hash` form the chain.
- `execution_ledger` — idempotency key → outcome. Survives restarts, which is
  what makes double-charging structurally impossible rather than unlikely.
- `recovery_attempts` — attempt history, read from the store and never from
  the event payload, so a caller cannot claim a lower retry count.
- `scheduled_jobs` — deferred retries with their eligibility time.

Deployment note: this requires a **persistent disk**. On ephemeral serverless
storage the audit chain and the idempotency ledger would reset per instance,
which reintroduces exactly the double-charge risk they exist to prevent.

---

## Frontend

React 19 + Vite + TypeScript, no UI library. Custom five-token design system.
Three views: Overview, Cases, Agent.

Batches stream over SSE (`/run-batch/stream`), one frame per case as the
pipeline finishes it, so a run with live reasoning reads as work in progress
rather than a long silence.

200 unit tests plus 14 Playwright checks, including WCAG AA contrast sampling
of every visible text node in both themes and layout regression at six
viewport widths.
