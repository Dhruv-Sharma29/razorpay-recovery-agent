# Failed-Payment & Subscription Recovery Agent

An intelligent payment-recovery system that detects failed Razorpay payments, diagnoses the failure, applies bounded recovery policies, uses Qwen to explain the decision, executes permitted actions in Razorpay test mode, and records a complete audit trail.

## Overview

Failed payments can silently cause revenue leakage. This project automates safe recovery of eligible failed payments while ensuring that every action is bounded, explainable, and auditable.

The system is designed around a strict separation:

> **Rules decide. Qwen explains. The executor acts. The audit log records.**

Qwen never independently authorizes a recovery action.

## Architecture

```text
                 Razorpay Test Events
                         │
                         ▼
                ┌─────────────────┐
                │    Ingestion    │
                │ Webhook / Batch │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ Failure         │
                │ Classifier      │
                │ Rules-first     │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │ Decision Policy │
                │ Bounded / Gated│
                └────────┬────────┘
                         │
                ┌────────┴────────┐
                ▼                 ▼
         ┌──────────────┐   ┌──────────────┐
         │ Qwen 3.5     │   │ Escalation   │
         │ Explanation  │   │ Handler      │
         └──────┬───────┘   └──────┬───────┘
                │                  │
                └─────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ Action Executor │
                 └────────┬────────┘
                          │
                          ▼
                  Razorpay Test API
                          │
                          ▼
                 ┌─────────────────┐
                 │   Audit Log     │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │ React Dashboard │
                 └─────────────────┘
```

## Core Principle

Every automated recovery action must be traceable through:

```text
CAUSE → RULE → BOUND → ACTION → OUTCOME
```

The deterministic policy engine is the authority for recovery decisions.

Qwen 3.5, running locally through Ollama, is used only to generate a human-readable explanation of an already-made decision.

## Failure Taxonomy

The system initially supports:

| Failure Category             | Example Signal            |
| ---------------------------- | ------------------------- |
| Insufficient Funds           | `INSUFFICIENT_FUNDS`      |
| Expired / Inactive Mandate   | Expired or paused mandate |
| Bank / Gateway Timeout       | `GATEWAY_ERROR`           |
| Card Declined                | `CARD_DECLINED`           |
| Authentication / OTP Failure | OTP / 3DS-related error   |
| Unknown / Ambiguous          | No clean rule match       |

The classifier is rules-first, deterministic, explainable, and testable.

## Recovery Policy

The policy engine applies bounded actions based on failure category, attempt history, transaction amount, cooldowns, and escalation conditions.

| Root Cause               | Action                              | Limit             |
| ------------------------ | ----------------------------------- | ----------------- |
| Insufficient Funds       | Retry after cooldown                | Maximum 2 retries |
| Expired / Paused Mandate | Trigger re-authorization            | 1 attempt         |
| Bank / Gateway Timeout   | Immediate retry                     | 1 retry           |
| Card Declined            | Alternate saved method if available | 1 switch          |
| Authentication Failure   | Resend authentication prompt        | 1 resend          |
| Unknown                  | No automatic action                 | Always escalate   |

### Global Safety Rules

* Maximum of 3 automated attempts per transaction.
* Cooldowns must be enforced.
* Automatic recovery is disabled above the configured amount threshold.
* Unknown failures are escalated.
* Every escalation is logged.
* Every stop is logged.
* Every exception is logged.
* Qwen cannot override policy decisions.

## Qwen 3.5 + Ollama

Qwen runs locally through Ollama.

```text
FastAPI
   │
   ▼
ReasoningService
   │
   ▼
Ollama
   │
   ▼
Qwen 3.5
```

Qwen receives structured information such as:

* payment event
* diagnosed cause
* matched rule
* policy decision
* attempt number
* relevant limits

It returns a human-readable explanation.

Example:

```json
{
  "policy_decision": {
    "allowed": true,
    "action": "scheduled_retry"
  },
  "reasoning": {
    "success": true,
    "text": "The payment failed because the available funds were insufficient. This is within the permitted retry limit and the transaction is below the automatic-recovery threshold, so the policy allows a retry after the required cooldown."
  }
}
```

If Qwen or Ollama is unavailable, the deterministic policy decision remains valid and the failure is recorded in the audit log.

## Golden Path

The primary live demonstration is:

```text
Failed payment
      ↓
Insufficient-funds classification
      ↓
Deterministic policy decision
      ↓
Qwen explanation
      ↓
Bounded retry
      ↓
Razorpay test-mode execution
      ↓
Outcome
      ↓
Audit log
      ↓
Dashboard
```

## Audit Trail

Each recovery event records information such as:

```text
event_id
diagnosed_cause
confidence
rule_fired
action_taken
scheduled_for
attempt_number
outcome
amount_recovered
reasoning
timestamp
```

This makes every recovery decision inspectable and explainable.

## Dashboard

The React dashboard displays:

* Total failed payments
* Recovery rate
* Amount recovered
* Escalations
* Exceptions
* Live audit trail

Each event should make the following sequence visible:

```text
CAUSE
  ↓
RULE
  ↓
BOUND
  ↓
ACTION
  ↓
OUTCOME
```

## Technology Stack

### Backend

* Python
* FastAPI
* Pydantic
* SQLite
* pytest

### AI Reasoning

* Qwen 3.5
* Ollama

### Payments

* Razorpay Test Mode

### Frontend

* React
* TypeScript
* Vite

### Development

* Git
* GitHub
* Cursor
* Antigravity
* GitHub Copilot

## Project Structure

```text
recovery-agent/
│
├── AGENTS.md
├── README.md
├── recovery-agent-implementation-plan-v2.md
├── .gitignore
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── models/
│   │   ├── ingestion/
│   │   ├── classifier/
│   │   ├── policy/
│   │   ├── reasoning/
│   │   ├── razorpay/
│   │   ├── executor/
│   │   ├── escalation/
│   │   └── audit/
│   │
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── api/
│       └── types/
│
├── data/
│   ├── synthetic/
│   └── held_out/
│
└── tests/
    ├── classifier/
    ├── policy/
    ├── reasoning/
    └── integration/
```

## Local Development

### Start Ollama

Make sure Ollama is running and the Qwen model is available:

```bash
ollama list
```

Run the model:

```bash
ollama run qwen3.5:latest
```

### Backend Setup & Start

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend Setup & Start

```bash
cd frontend
npm install
npm run dev
```

## Environment Variables

Create a local `.env` file using `.env.example`.

Typical configuration:

```text
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:latest

DATABASE_URL=
AUTO_RECOVERY_AMOUNT_LIMIT=
```

Never commit `.env` or API secrets.

## Testing

Run backend tests:

```bash
pytest
```

Run frontend build:

```bash
npm run build
```

The project should test both successful recovery paths and failure paths.

Important tests include:

* Retry-limit enforcement
* Cooldown enforcement
* Amount-cap enforcement
* Unknown-failure escalation
* Duplicate-event handling
* Razorpay API failure
* Ollama failure
* Malformed Qwen output
* Audit logging
* Policy/Qwen separation
* End-to-end recovery flow

## Evaluation

The system should evaluate performance using a development dataset and a held-out dataset.

Report:

* Overall recovery rate
* Recovery rate by category
* Amount recovered
* Escalation rate
* Exception count
* False-escalation cost estimate

The held-out dataset must remain untouched during development.

## Safety Model

This system is designed around bounded automation.

```text
                 Qwen
                  │
              explains
                  │
                  ▼
Payment → Classifier → Policy → Executor
                         │
                      decides
                         │
                    ┌────┴────┐
                    ▼         ▼
                 Allowed   Escalate
                    │
                    ▼
                Razorpay
```

The model is never the final authority over a payment action.

## Demo Scenario

The recommended demonstration shows:

1. A failed payment enters the system.
2. The classifier identifies the root cause.
3. The policy engine determines whether recovery is allowed.
4. Qwen generates an explanation.
5. The permitted action executes in Razorpay test mode.
6. The audit trail updates.
7. The dashboard displays the result.
8. A later attempt demonstrates escalation.
9. A deliberate API/model failure demonstrates graceful exception handling.

## Future Improvements

Possible future extensions include:

* Natural-language audit-log queries
* Additional recovery flows
* Machine-learning classification for ambiguous failures
* Customer notification workflows
* Promise-to-pay tracking
* Additional payment channels

These features should only be added after the golden path is reliable.