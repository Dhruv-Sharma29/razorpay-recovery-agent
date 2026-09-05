# Reflow

**A focused revenue-recovery agent for failed payments and subscription renewals.**

![python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![fastapi](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)
![react](https://img.shields.io/badge/frontend-React%2019-149eca?logo=react&logoColor=white)
![executor](https://img.shields.io/badge/default%20executor-simulated-lightgrey)

A safety-first AI-assisted recovery pipeline for failed Razorpay-style payment events. The system asks NVIDIA NIM (Nemotron) to detect revenue at risk and recommend a candidate intervention, independently classifies failures with deterministic rules, validates the recommendation through bounded recovery policy, executes only policy-approved actions in a sandbox executor, and appends every result to a SQLite audit log for the React dashboard.

> **Nemotron detects and recommends. Rules constrain and authorize. The executor acts. The audit log records.**

## Demo

![Reflow recovery console](docs/demo.gif)

When the frontend is served behind a trusted proxy that supplies the API key, the fastest browser demo is the dashboard's **Run golden path** action. It creates a fresh, below-cap insufficient-funds event and sends it through the complete pipeline.

## Current implementation status

The golden path is implemented end to end with synthetic/Razorpay-shaped events:

```text
payment event → rules-first classification → bounded policy decision
→ AI risk recommendation → rules-first classification → bounded policy gate
→ Nemotron explanation (or safe fallback) → simulated execution → escalation/audit → dashboard
```

The default `SimulatedPaymentExecutor` (also available as the backwards-compatible `MockExecutor`) makes no network calls. Authorized actions are reported as simulated captures so the demo can measure recovered amount without touching a payment gateway.

An opt-in `RazorpayTestExecutor` is included. Set `EXECUTOR_MODE=razorpay_test` only with Razorpay test keys. The adapter refuses non-test key IDs (`rzp_test_`) and re-checks the configured amount cap before making a request. It is a sandbox integration for this project, not a production payment adapter.

NIM is optional. Without `NIM_API_KEY`, the pipeline remains fully usable and records a deterministic, policy-grounded fallback explanation.

## Architecture

```text
                         Failed payment event
                                  │
                                  ▼
                       ┌────────────────────┐
                       │ Ingestion / Pydantic│
                       │ event validation    │
                       └──────────┬─────────┘
                                  ▼
                       ┌────────────────────┐
                       │ RecoveryRecommender│
                       │ Nemotron via NIM   │
                       │ advisory candidate │
                       └──────────┬─────────┘
                                  ▼
                       ┌────────────────────┐
                       │ FailureClassifier  │
                       │ deterministic rules│
                       └──────────┬─────────┘
                                  ▼
                       ┌────────────────────┐
                       │ RecoveryPolicyEngine│
                       │ bounded authority   │
                       └───────┬────────────┘
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
       ┌────────────────────┐      ┌──────────────────┐
       │ RecoveryReasoner   │      │ EscalationHandler│
       │ final explanation  │      │ fail-closed      │
       │ explanation only   │      └────────┬─────────┘
       └──────────┬─────────┘               │
                  └──────────────┬─────────┘
                                 ▼
                       ┌────────────────────┐
                       │ RecoveryExecutor   │
                       │ simulated or test  │
                       └──────────┬─────────┘
                                  ▼
                       ┌────────────────────┐
                       │ Append-only SQLite │
                       │ AuditLogger        │
                       └──────────┬─────────┘
                                  ▼
                       ┌────────────────────┐
                       │ React + TypeScript │
                       │ operations dashboard│
                       └────────────────────┘
```

Every event is traceable as:

```text
CAUSE → RULE → BOUND → ACTION → OUTCOME
```

Nemotron receives the normalized event and deterministic evidence to produce an advisory risk/recommendation result, then receives the final policy decision to explain it. Its recommendation is untrusted and cannot authorize execution. If NIM is unavailable, times out, or returns malformed JSON, the pipeline uses deterministic fallback data without changing the policy decision.

## Failure taxonomy

| Category | Typical signals | Policy action |
| --- | --- | --- |
| `insufficient_funds` | Balance/insufficient-funds message or code | Scheduled retry |
| `expired_card` | Expired card or inactive mandate signal | Trigger re-authorization |
| `network_error` | `GATEWAY_ERROR`, timeout, network failure | One immediate retry |
| `bank_decline` | Issuer/card decline signal | Switch payment method |
| `authentication_failure` | OTP, 3DS, or authentication failure | Resend auth prompt |
| `unknown` | No clean rule match | No automatic action; escalate |

Classification is local, deterministic, and rules-first. Specific error codes take precedence over message patterns; ambiguous events fail closed.

## Recovery policy and safety limits

| Root cause | Action | Category limit |
| --- | --- | --- |
| Insufficient funds | Retry after the 24-hour policy cooldown | Maximum 2 retries |
| Expired card/mandate | Trigger re-authorization | 1 attempt |
| Network/gateway error | Immediate retry | 1 retry |
| Bank decline | Switch to an alternate method | 1 switch |
| Authentication failure | Resend authentication prompt | 1 resend |
| Unknown | Escalate | 0 automatic actions |

Global guards are enforced by `RecoveryPolicyEngine`:

- Maximum 3 total automated attempts per transaction.
- Automatic recovery is disabled above `AUTO_RECOVERY_AMOUNT_LIMIT` (default: `500000` paise / ₹5,000).
- Invalid amounts, missing classifications, exhausted limits, and unknown failures escalate.
- The executor runs only when `automatic_recovery_allowed` is true.
- Escalations, stops, execution failures, reasoning failures, and audit failures are recorded.
- Neither Nemotron nor the dashboard can authorize or expand a recovery action.

## API

`GET /health` is public. All `/api/dashboard/*` routes require the `X-API-Key` header matching `API_SECRET_KEY`.

Start the backend from the `backend/` directory. It exposes:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service health check |
| `POST` | `/api/dashboard/process` | Process one validated payment event |
| `GET` | `/api/dashboard/audit` | Read the append-only audit records |
| `GET` | `/api/dashboard/audit/export` | Export the audit log as CSV |
| `POST` | `/api/dashboard/run-batch` | Process N fresh synthetic failures and report measured recovery |
| `POST` | `/api/dashboard/run-scheduled` | Execute deferred retries whose cooldown has elapsed (`?now=` to skip the wait) |
| `GET` | `/api/dashboard/scheduled` | List scheduled retry jobs |
| `GET` | `/api/dashboard/risk` | Revenue-at-risk rollups by merchant, repeat customer, and subscription |
| `GET` | `/api/dashboard/telemetry` | Recovery, fallback, cache, and latency metrics |
| `GET` | `/api/dashboard/provider` | NIM provider/model status without exposing the API key |
| `POST` | `/api/dashboard/golden-path` | Run a fresh insufficient-funds demo event |
| `POST` | `/api/dashboard/reset` | Clear recovery state for a clean demo. Never clears audit history |

Amounts are supplied in the smallest currency unit (for INR, paise). A minimal request looks like:

```json
{
  "event_id": "evt_demo_001",
  "razorpay_payment_id": "pay_test_demo_001",
  "merchant_id": "merch_01",
  "customer_id": "cust_001",
  "type": "one_time",
  "amount": 149900,
  "currency": "INR",
  "payment_method": "upi",
  "error_code": "INSUFFICIENT_FUNDS",
  "error_description": "Payment failed due to insufficient funds",
  "failure_category": "unknown",
  "attempt_number": 1,
  "mandate_status": null,
  "timestamp": "2026-09-02T10:00:00Z"
}
```

The submitted `failure_category` is part of the validated event schema; the backend independently derives the effective category with `FailureClassifier`.

Audit records persist to `DATABASE_URL` (default `sqlite:///./recovery.db`), so they survive a backend restart. `GET /api/dashboard/audit` supports pagination and filtering: `?limit=&offset=` page the log (omit `limit` to return all) and `?outcome=recovered` filters by final outcome; the response includes `count` (this page) and `total` (all matching records).

`POST /api/dashboard/run-batch` accepts `count` from 1 to 500. Use `seed=` for a reproducible synthetic batch, `run_scheduler=true` to complete deferred retries before measuring, and `explain=true` to make live NIM calls for each event. Batch AI recommendations and explanations are skipped by default because they are advisory and do not affect recovery metrics.

## Quick start

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

For a protected local API, add a development-only secret to `backend/.env`:

```dotenv
API_SECRET_KEY=local-dev-secret
EXECUTOR_MODE=mock
```

Start FastAPI:

```bash
uvicorn app.main:app --reload --port 8000
```

NIM is optional for the pipeline: without a reachable NIM API, AI recommendations and explanations become safe deterministic fallbacks. To use live recommendations and explanations, set `NIM_API_KEY` and ensure the model named by `NIM_MODEL` is available on the NIM catalog.

The API key must be sent as `X-API-Key` on dashboard requests. For example:

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/api/dashboard/golden-path \
  -H "X-API-Key: local-dev-secret"
```

### Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal (normally `http://localhost:5173`). The frontend calls `http://localhost:8000` by default. Set `VITE_API_BASE` if the backend is hosted elsewhere.

The current frontend API client does not embed or send `API_SECRET_KEY`. To use the browser dashboard against the protected API, keep the key server-side and add the header at a trusted reverse proxy or other server-side boundary; do not put a production API secret in a public Vite bundle. Direct CLI/API requests should use the `curl` pattern above.

## Demo and evaluation

Run the five safety/demo scenarios from `backend/`:

```bash
python verify_demo.py
```

This covers a recoverable insufficient-funds event, retry-limit escalation, unknown-failure escalation, amount-cap escalation, and a simulated executor failure.

Generate the deterministic 80-event dataset (80% development, 20% held-out):

```bash
python -m app.ingestion.generator
```

Run evaluation against both datasets:

```bash
python evaluate.py
```

Evaluation reports are written to `backend/evaluation_results/` and include classification accuracy, automatic recoveries, escalations, execution failures, unknown/unsafe cases, and false automatic recoveries.

### Evaluation integrity

Classification is driven by the structured `error_code` — the same signal a real Razorpay integration receives — not by keyword-matching the event's free-text description. The generator keeps descriptions independent of the classifier's message rules, and the held-out slice is re-worded from a disjoint phrase pool, so held-out accuracy measures genuine generalization to unseen wording rather than the dataset echoing the classifier's own keywords back at it.

## Tests

Backend tests:

```bash
cd backend
python -m pytest -q
```

Frontend tests and production build:

```bash
cd frontend
npm test
npm run build
```

The test suite covers classification and precedence, every policy rule and stopping rule, fail-closed behavior, reasoning fallbacks and policy isolation, executor idempotency, scheduler behavior, audit persistence/redaction, API validation and authentication, pipeline integration, dashboard rendering, evaluation metrics, and security hardening.

## Verification

Last verified locally on 2026-09-04:

- Backend: 453 tests passed; 2 deselected
- Frontend: 112 tests passed
- Production build: successful
- `git diff --check`: clean

Run the checks again from a clean checkout with:

```bash
cd backend
python -m pytest -q

cd ../frontend
npm test
npm run build
```

## Project structure

```text
.
├── backend/
│   ├── app/
│   │   ├── audit/          # append-only SQLite audit store
│   │   ├── auth.py         # API-key protection for dashboard routes
│   │   ├── classifier/     # deterministic failure taxonomy
│   │   ├── escalation/     # human-review/fail-closed handling
│   │   ├── evaluation/     # synthetic and held-out evaluation
│   │   ├── executor/       # executor contract and simulated executor
│   │   ├── ingestion/      # event generator and loader
│   │   ├── models/         # Pydantic payment-event schema
│   │   ├── persistence/    # durable retry/idempotency state
│   │   ├── pipeline/       # end-to-end orchestration
│   │   ├── policy/         # authoritative bounded policy engine
│   │   ├── razorpay/       # opt-in Razorpay test-mode adapter
│   │   ├── recommendation/ # Nemotron/NIM risk advisor and fallback
│   │   ├── reasoning/      # Nemotron/NIM explainer and fallback
│   │   ├── scheduler/      # deferred retry worker
│   │   ├── dashboard.py    # FastAPI dashboard endpoints
│   │   └── main.py         # FastAPI application
│   ├── tests/
│   ├── evaluate.py
│   └── verify_demo.py
├── data/
│   ├── synthetic/          # development dataset
│   └── held_out/           # held-out evaluation slice
└── frontend/
    └── src/                # React dashboard and API client
```

## Deployment

The frontend and the API deploy separately, and that split is deliberate.

**Frontend → Vercel.** It is a static Vite build, which is exactly what
Vercel is for.

1. New Project → import the repo → set **Root Directory** to `frontend`.
   `frontend/vercel.json` supplies the rest.
2. Add `VITE_API_BASE` pointing at the deployed API (no trailing slash). It
   is baked in at build time, so redeploy after changing it.

**API → a host with a persistent disk**, *not* Vercel serverless. The audit
log and the idempotency ledger are SQLite files. Serverless filesystems are
ephemeral and are not shared between concurrent invocations, so on Vercel
the append-only audit trail would be lost on every cold start and the same
payment could be retried twice — the two guarantees this system is built
on. `backend/Dockerfile` and `backend/render.yaml` deploy it to Render's
free tier with a 1 GB disk mounted at `/data`:

1. New → Blueprint → point at `backend/render.yaml`.
2. Set `NIM_API_KEY` (leave unset to run on the deterministic fallback).
3. Set `CORS_ALLOW_ORIGINS` to your Vercel URL, comma-separated for more
   than one.

Then set `VITE_API_BASE` on Vercel to the Render URL and redeploy.

Any host that runs a container with a mounted volume works the same way —
Railway, Fly.io, or a plain VM. If you later want true serverless, the
storage layer (`app/audit/store.py`, `app/persistence/store.py`) is raw
`sqlite3` and would need porting to Postgres first.

### Deployment environment variables

| Where | Variable | Purpose |
| --- | --- | --- |
| Vercel | `VITE_API_BASE` | Deployed API origin, no trailing slash |
| API | `DATABASE_URL` | `sqlite:////data/recovery.db` on the mounted disk |
| API | `CORS_ALLOW_ORIGINS` | Comma-separated allowlist; never a wildcard |
| API | `NIM_API_KEY` | Optional; without it reasoning uses the fallback |
| API | `NIM_MODEL` | Defaults to the Nemotron model |

## Configuration

Copy `backend/.env.example` to `backend/.env` and adjust as needed:

```dotenv
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
NIM_API_KEY=
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NIM_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning
DATABASE_URL=sqlite:///./recovery.db
AUTO_RECOVERY_AMOUNT_LIMIT=500000
EXECUTOR_MODE=mock
ENVIRONMENT=development
API_SECRET_KEY=local-dev-secret
CORS_ALLOW_ORIGINS=http://localhost:5173,http://localhost:3000
```

`EXECUTOR_MODE=mock` keeps the demo offline. Use `razorpay_test` only with test credentials; the adapter refuses key IDs that do not begin with `rzp_test_`. `API_SECRET_KEY` protects all dashboard routes and must be sent as `X-API-Key`.

Do not commit `.env` files, Razorpay credentials, or model/API tokens. The audit logger recursively redacts credential-like fields before persistence.

## Design boundary

The project intentionally keeps authorization deterministic:

1. `RecoveryRecommender` detects risk and suggests a cause/action candidate; it has no recovery authority.
2. `FailureClassifier` independently identifies the failure category.
3. `RecoveryPolicyEngine` decides whether an action is allowed and which bounds apply, accepting or constraining the candidate.
4. `RecoveryReasoner` explains the final decision; it cannot modify it.
5. `RecoveryExecutor` performs only an authorized action and returns a structured result.
6. `EscalationHandler` routes denied/failed/unsafe cases without authorizing recovery.
7. `AuditLogger` appends the complete outcome for inspection.

This boundary is the core safety property of the recovery agent.
