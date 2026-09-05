# Reflow — AI Revenue Recovery

**Track 03 · Razorpay Buildathon**

A bounded agent that finds revenue slipping away, decides what to do about it,
and — crucially — knows when to stop.

---

## The claim

Recovering failed payments is easy to fake. Retry everything, count the ones
that land, report a big number. It works right up until you double-charge a
customer or burn an issuer relationship.

Reflow is built the other way round. **A deterministic policy engine is the
sole authority on whether money moves.** The language model advises, and can
choose between actions the policy has already authorised — it can never add
one, raise a limit, or overturn a refusal.

Everything below is measured, reproducible from a seed, and reported whichever
way it falls.

---

## What one batch actually does

100 synthetic failed payments, seed 11:

| | |
|---|---|
| Attempted | ₹12,25,675 |
| Policy authorised chasing | ₹32,652 |
| **Recovered** | **₹22,487 — 69% of what was recoverable** |
| Recovered / pending / escalated | 11 / 3 / 86 |
| Median time to recovery | 0s (6 inline, 5 after a cooldown) |

The number that matters is **69% of recoverable**, not 1.8% of everything that
failed. The gap between those two figures is the product: 86 of 100 payments
were deliberately not chased.

## The 86 escalations are the point

A dashboard shows escalations as failure. They are the opposite. Here is what a
retry-everything agent would have done with the same batch:

| | |
|---|---|
| Extra issuer attempts it would have fired | **85** |
| Amount it would have chased past the cap | **₹11,84,152** |
| Attempts past the hard retry limit | 4 |
| Blind retries on an unclassifiable failure | 2 |

Each of those 85 attempts is a real issuer hit and a chance to charge someone
twice. This is derived from the policy's own refusal reasons, so it is a
restatement of decisions actually made — not a second simulation.

## We measured whether the AI earns its place

Most projects assert their model helps. `POST /api/dashboard/run-ab` runs the
same batch twice, differing in exactly one variable: whether the advisor may
choose the action from the permitted set.

The measured result, 100 events against the live model:

| Arm | Recovered | Rate |
|---|---|---|
| policy default action | ₹22,487 | 69% |
| advisor chose among permitted actions | ₹22,487 | 69% |
| **Difference** | **₹0** | **0pp** |

The advisor answered on **66 events** and **agreed with policy every time**,
across the 6 that offered a genuine choice.

That is a finding, not a failure. Given the permitted menu and the recovery
rates measured from its own audit log, a reasoning model independently
arrives at the same action the deterministic rules already chose. The rules
encode what the model would conclude anyway — so the model adds explanation
and customer language without adding risk.

We report that. It is why the model's authority is bounded and why a confidence
floor gates its choices. The architecture means a wrong model costs us three
suboptimal-but-authorised actions, never an unsafe one.

The ceiling is honest too: on this batch only **6 events** have more than one
permitted action. For the other 94, policy is decisive and there is nothing to
decide.

An earlier run, before the advisor was given the permitted-action menu and the
measured outcomes, had it changing 3 of 60 actions and losing ₹696. Telling it
what was available and what had actually worked is what closed that gap.

## It learns from its own record

Recovery rates are aggregated from the append-only audit log and fed back into
the advisor's prompt — measured, not configured:

```
insufficient_funds   scheduled_retry          80%   4/5
authentication_...   resend_auth_prompt       67%   2/3
bank_decline         switch_payment_method    40%   2/5
```

Run a batch, then run another, and the agent knows more than it did.

---

## The bar

All three of the brief's named surfaces are covered end to end:

- **Payment failures** — classified, bounded, retried or refused
- **Overdue receivables** — a 72-hour chaser, because you do not retry a rail
  that never declined
- **Checkout abandonment** — a one-hour nudge with its own action, because
  nothing was declined and there is no mandate to charge; the only lever is
  asking the customer back, and it stops at two messages

Subscriptions are partial: mandate status and tokenised charges exist, but
there is no subscription-specific policy rule yet.

| Requirement | How |
|---|---|
| Measured money recovered across a batch | Per-cause capture model, reported in ₹ and as % of recoverable |
| Compliant escalation | Every refusal carries a machine-readable reason |
| Stopping rules | Amount cap, per-cause retry limits, a global attempt cap, cooldowns — and the cost avoided is quantified |
| Audit trail | Append-only and **hash-chained**: each record carries the previous record's hash |

**587 backend tests, 200 frontend, 14 end-to-end** including WCAG AA contrast
checks in both themes.

---

## What we did not do

- **No operator override buttons.** A human clicking "Execute" past the policy
  engine would undo the entire guarantee.
- **No headline number the code cannot produce.** Every figure here comes from
  a seeded run you can reproduce.
- **The executor is honest about simulation.** `simulated: true` rides on every
  result until a real gateway is actually called.

## Real Razorpay

A failed payment is terminal at Razorpay — there is no retry endpoint, and
`capture` only applies to already-authorised payments. So recovery is always a
*new* attempt: a fresh order plus either a tokenised charge (customer absent)
or a payment link (customer present). The executor routes on the policy action
and refuses, explicitly, when the prerequisites are missing — a gap in the
integration must never look like a customer who declined.

`GET /api/dashboard/razorpay-check` reports whether credentials would actually
reach Razorpay, and distinguishes valid-but-simulated from genuinely live.
