# Reflow — 5-minute pitch

Doubles as the shot list for the demo recording. Timings are a guide, not a
script to read aloud.

Before recording: start the backend on `:8000` and the frontend on `:5173`
(the CORS allowlist expects that port), then `POST /api/dashboard/reset` so
recovery state is clean. Audit history is append-only and will persist — that
is the point, so do not apologise for it on camera.

---

## 0:00 – 0:35 · The problem

Failed payments are not an edge case, they are revenue sitting on the floor.
A card expires, a balance is short, a gateway times out — and the money is
simply gone unless something chases it.

The naive fix is "retry everything". That is how you double-charge a customer,
burn through an issuer's tolerance, and turn one failure into a support
ticket. The interesting problem is not *retrying*, it is **deciding when not
to**.

## 0:35 – 1:45 · Live single-event demo

Open **Agent**. Click **Recoverable failure**.

Walk the rail left to right, one sentence each:

1. **Classification** — deterministic, from the error code. Not a model.
2. **Policy decision** — the bounded rule that fired, with its limits.
3. **Reasoning** — badged *Advisory only*. This is where the model speaks, and
   the badge says whether the text came from the model or the deterministic
   fallback. It explains; it never decides.
4. **Execution** — what actually ran.
5. **Escalation** — why a human was or was not needed.
6. **Transaction info** — the money.

Land on the measured result: **₹1,499 recovered, payment captured
(simulated)**. Say the word *simulated* out loud — no real gateway was
called, and the UI says so everywhere.

## 1:45 – 2:30 · Watch it refuse

Still on **Agent**, click the three outlined buttons in turn:

- **Over amount cap (₹90,000)** — above the ₹5,000 auto-recovery ceiling.
- **Past retry limit (attempt 5)** — above the 3-attempt hard cap.
- **Unknown failure cause** — no rule matched.

All three escalate. This is the part worth dwelling on: *the agent's most
valuable behaviour is refusing.* An unknown failure is never auto-recovered,
because "we do not know what this is" is not a licence to move money.

## 2:30 – 3:30 · Batch results — measured money

Go to **Overview**. Set the count to 40 and hit **Run batch**.

Four numbers, in order:

- **Revenue at risk** — everything that failed.
- **Confirmed recovered** — money a simulated capture actually took, not an
  assumption from a success flag.
- **Recovery rate** — *by amount*, not by count, because one large recovery is
  worth more than five small ones.
- **Signals detected** — how many cases warranted acting at all.

Then the funnel. Say the caption out loud: **each stage is a real filter, not
a restatement of the one above it.** Raw → needed a signal → contacted →
confirmed recovered. Most dashboards quietly restate the same number four
times; this one narrows.

Click **Run batch** again to show it is repeatable — fresh event ids, results
accumulate.

## 3:30 – 4:20 · The safety story

Go to **Cases**, expand any row.

Read the chain aloud: **cause → rule → bound → action → outcome → recovered
amount.** Every record carries the rule id that classified it, the policy rule
that decided it, the limits that bounded it, and the money that moved. You can
audit one row without replaying anything.

Three guarantees worth naming:

- **The model cannot authorize.** `policy_action_allowed` is copied verbatim
  from the policy engine. A model replying "approve and retry" changes
  nothing.
- **The audit log is append-only.** There is a test that fails if `UPDATE` or
  `DELETE` appears in the audit module. Reset clears recovery state; it never
  clears history.
- **Customer identifiers are never stored.** Repeat failures are grouped by a
  pseudonymous reference instead.

## 4:20 – 5:00 · What broke, and close

Pick two from the README's *What broke, and how we fixed it* — the
paise/rupee boundary and the dataset/classifier circularity are the most
honest ones. The second is worth saying plainly: the generator labels each
event, so a classifier that read that label back would have scored 100% and
proven nothing.

Close on the tagline:

> **Rules decide. Nemotron explains. The executor acts. The audit log
> records.**
