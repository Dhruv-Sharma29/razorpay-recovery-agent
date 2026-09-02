# Recovery Agent

## Source of Truth

The primary specification is:

recovery-agent-implementation-plan.md

Read it before making architectural changes.

## Architecture

The system contains:

1. Ingestion
2. Rules-first failure classification
3. Bounded decision policy
4. Qwen reasoning/explanation
5. Action executor
6. Escalation handler
7. Append-only audit log
8. React dashboard

## AI Boundary

The deterministic policy engine decides the recovery action.

Qwen only explains the decision.

Qwen must NEVER:
- authorize a payment action
- bypass a policy
- increase retry count
- bypass cooldown
- bypass amount cap
- suppress escalation

## Safety

Unknown failures must not auto-recover.

All stops and escalations must be logged.

All automated actions must respect policy limits.

## Golden Path

The primary live demo is:

insufficient funds
→ classification
→ policy decision
→ Qwen explanation
→ bounded retry
→ Razorpay test mode
→ audit log
→ dashboard

## Testing

Every policy rule requires unit tests.

Stopping rules require explicit tests.

Failure paths are as important as success paths.

## Git

Never directly modify main.

Use feature branches.

## Scope

Golden path first.

Do not build stretch features before the golden path works.

Never commit secrets.