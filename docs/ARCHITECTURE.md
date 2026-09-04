# Reflow — architecture

Deck source. One `##` section per slide.

---

## 1 · What this is

A bounded recovery agent for failed payments and subscription renewals.

It answers one question — **how much of the money that just failed can we get
back, safely** — and it is built so that every rupee it claims can be traced
to a rule that authorized it.

> Rules decide. Nemotron explains. The executor acts. The audit log records.

---

## 2 · The pipeline

```text
                       failed payment event
                                │
                                ▼
                   ┌────────────────────────┐
                   │ 1. Classification      │  deterministic, rules-first
                   │    error_code → cause  │  never the model
                   └───────────┬────────────┘
                               ▼
                   ┌────────────────────────┐
                   │ 2. Policy engine       │  THE ONLY AUTHORITY
                   │    action + bounds     │  retry caps, amount cap,
                   └───────────┬────────────┘  cooldown, stopping rules
                               ▼
                   ┌────────────────────────┐
                   │ 3. Reasoning (NIM)     │  ADVISORY ONLY
                   │    explains the above  │  cannot authorize
                   └───────────┬────────────┘  falls back deterministically
                               ▼
              ┌────────────────┴────────────────┐
              ▼                                 ▼
   ┌────────────────────┐            ┌────────────────────┐
   │ 4a. Execute now    │            │ 4b. Schedule       │
   │  no cooldown       │            │  cooldown > 0      │
   └─────────┬──────────┘            └─────────┬──────────┘
             │                                 │  pending, ₹0 claimed
             │                                 ▼
             │                       ┌────────────────────┐
             │                       │ Scheduler worker   │
             │                       │ runs when eligible │
             │                       └─────────┬──────────┘
             └────────────────┬────────────────┘
                              ▼
                   ┌────────────────────────┐
                   │ 5. Escalation          │  fail-closed
                   └───────────┬────────────┘
                               ▼
                   ┌────────────────────────┐
                   │ 6. Audit log           │  append-only
                   └────────────────────────┘
```

---

## 3 · The decision chain

Every audit record carries its own full provenance, so a reviewer can audit
one row without replaying the pipeline:

| Link | Example |
| --- | --- |
| **Cause** | `network_error` via `code.gateway_error` |
| **Rule** | `policy.network_error.immediate_retry` |
| **Bound** | cap ₹5,000 · max 1 retry · 0s cooldown |
| **Action** | `immediate_retry` |
| **Outcome** | `recovered` · `captured` |
| **Recovered** | ₹1,499.00 |

Deferred retries additionally record `scheduled_for`; escalations record
`escalation_trigger`; every record records whether the explanation came from
the model or the deterministic fallback.

---

## 4 · The safety boundary

**What the model may do:** explain a decision in plain language, say why the
action fits, draft customer-facing copy, summarise an escalation.

**What the model may never do:**

- authorize a payment action
- raise a retry count or an amount cap
- bypass a cooldown
- suppress an escalation
- reinterpret the failure category to unlock recovery

Enforcement is structural, not prompt-based. `policy_action_allowed` is copied
verbatim from the policy engine on both the model path and the fallback path.
A model that replies *"approve and retry immediately"* on a denied decision
changes nothing — there is a test for exactly that.

Customer-facing copy is additionally rejected, not sanitised, if it names an
amount or promises an outcome: money must never be implied to have moved
before a retry has run.

---

## 5 · Why "pending" matters

An insufficient-funds failure gets a 24-hour cooldown. Between the decision
and the retry, the honest status is **pending**, not recovered:

```text
failure → policy: retry after 24h → scheduled (₹0 claimed)
        → worker runs when eligible → captured → ₹1,499 recovered
```

Reporting that as "recovered" at decision time would inflate every headline
number in the product. The batch runner therefore runs the scheduler to
completion *before* measuring.

---

## 6 · Measuring money, not success flags

Recovery rate is reported **by amount**, because one large recovery outranks
five small ones. Recovered amounts come from the executor's simulated payment
result, never inferred from an outcome label.

The funnel narrows through four real filters:

```text
Raw event-cases      ██████████████████████  40
Needed a signal      ██████████████████████  40
Contacted            ███                      4
Confirmed recovered  ███                      4
```

The stage definitions live in the evaluation harness, so the funnel and the
KPIs cannot drift apart.

---

## 7 · Guarantees we kept, even when inconvenient

- **Append-only audit.** A test fails if `UPDATE`/`DELETE` appears in the
  audit module. `POST /reset` clears recovery state and never history.
- **No customer identifiers stored.** Repeat failures group by a pseudonymous
  `customer_ref`.
- **Idempotency is durable.** A retry is never executed twice, even across a
  restart, and the scheduler worker is safe to run repeatedly.
- **Fail-closed reasoning.** If NIM is unreachable, the explanation degrades
  to a deterministic fallback grounded in the rule that fired — labelled as
  such — and the policy decision is untouched.

---

## 8 · Honest scope

- The executor is **simulated**. No Razorpay API is called; every result is
  flagged `simulated: true` in the API and in the UI.
- Datasets are synthetic and generated locally.
- Classification is deterministic rules over `error_code`, not an AI
  classifier — the ground-truth label in the generated data is used only for
  evaluation, never as an input.
