"""Abstract base for recovery executors.

The executor abstraction enforces the contract between the policy engine
and the execution layer:

1. Only policy-authorized actions may be executed.
2. Execution never reinterprets the failure category or makes its own
   recovery decision.
3. Idempotency is enforced — the same payment/action is never executed
   twice.
4. The original payment event is never mutated.
5. Execution failures produce structured results, never exceptions that
   crash the pipeline.

Concrete implementations (e.g. ``MockExecutor``, a future Razorpay
adapter) implement ``_do_execute`` to perform the actual action.
"""

from __future__ import annotations

import abc
import hashlib
import logging
import uuid
from datetime import datetime, timezone

from app.executor.result import ExecutionResult, ExecutionStatus
from app.models.payment_event import FailedTransactionEvent
from app.persistence.store import RecoveryStateStore
from app.policy.result import PolicyAction, PolicyDecision

logger = logging.getLogger(__name__)

# Policy actions that should NEVER trigger automated execution.
_NON_EXECUTABLE_ACTIONS: frozenset[PolicyAction] = frozenset(
    {
        PolicyAction.ESCALATE,
        PolicyAction.NO_ACTION,
    }
)

# Policy actions that are valid for automated execution.
_EXECUTABLE_ACTIONS: frozenset[PolicyAction] = frozenset(
    {
        PolicyAction.SCHEDULED_RETRY,
        PolicyAction.IMMEDIATE_RETRY,
        PolicyAction.TRIGGER_REAUTHORIZATION,
        PolicyAction.SWITCH_PAYMENT_METHOD,
        PolicyAction.RESEND_AUTH_PROMPT,
    }
)


def compute_idempotency_key(
    payment_event: FailedTransactionEvent,
    policy_decision: PolicyDecision,
) -> str:
    """Derive a deterministic idempotency key from the payment context.

    The key is a SHA-256 hex digest of:
    - event_id
    - razorpay_payment_id
    - action
    - attempt_number

    This ensures the same payment + action + attempt is never executed
    twice, while different payments or attempts produce different keys.
    """
    raw = (
        f"{payment_event.event_id}"
        f"|{payment_event.razorpay_payment_id}"
        f"|{policy_decision.action.value}"
        f"|{payment_event.attempt_number}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class RecoveryExecutor(abc.ABC):
    """Abstract recovery executor.

    Subclasses implement ``_do_execute`` to perform the concrete action
    (mock, sandbox, or real Razorpay API).  All policy-gate checks,
    idempotency tracking, and result construction are handled here.

    The executor NEVER:
    - Reinterprets the failure category.
    - Makes its own recovery decision.
    - Overrides or weakens the policy decision.
    - Bypasses amount limits.
    - Mutates the payment event.
    """

    def __init__(self, state_store: RecoveryStateStore | None = None) -> None:
        # Fast in-memory idempotency cache for the current process.
        self._executed_keys: dict[str, ExecutionResult] = {}
        # Optional durable store: persists idempotency + attempt history so a
        # retry is never re-executed even across a restart. When None the
        # executor behaves exactly as before (in-memory only).
        self._state_store = state_store

    def execute(
        self,
        payment_event: FailedTransactionEvent,
        policy_decision: PolicyDecision,
    ) -> ExecutionResult:
        """Execute a recovery action if authorized by the policy engine.

        This is the main entry point.  It performs all safety checks
        before delegating to ``_do_execute``.

        Args:
            payment_event: The failed payment transaction event.
            policy_decision: The authoritative policy decision.

        Returns:
            A structured ``ExecutionResult`` — always safe to use.
            Never raises exceptions that would crash the pipeline.
        """
        now = datetime.now(timezone.utc)

        # --- Guard: malformed input ---
        if payment_event is None or policy_decision is None:
            return ExecutionResult(
                status=ExecutionStatus.INVALID,
                action_attempted="unknown",
                payment_id="unknown",
                event_id="unknown",
                executed=False,
                idempotency_key="",
                error="Payment event or policy decision is None",
                reason="Cannot execute with missing input",
                timestamp=now,
            )

        event_id = payment_event.event_id
        payment_id = payment_event.razorpay_payment_id
        action = policy_decision.action
        action_str = action.value

        # Compute idempotency key before any checks
        try:
            idem_key = compute_idempotency_key(payment_event, policy_decision)
        except Exception as exc:
            logger.warning("Failed to compute idempotency key: %s", exc)
            return ExecutionResult(
                status=ExecutionStatus.INVALID,
                action_attempted=action_str,
                payment_id=payment_id,
                event_id=event_id,
                executed=False,
                idempotency_key="",
                error=f"Idempotency key computation failed: {exc}",
                reason="Cannot execute without a valid idempotency key",
                timestamp=now,
            )

        # --- Guard: policy denied recovery ---
        if not policy_decision.automatic_recovery_allowed:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                action_attempted=action_str,
                payment_id=payment_id,
                event_id=event_id,
                executed=False,
                idempotency_key=idem_key,
                error=None,
                reason=(
                    f"Policy denied automatic recovery: {policy_decision.reason}"
                ),
                timestamp=now,
            )

        # --- Guard: escalation / no-action are non-executable ---
        if action in _NON_EXECUTABLE_ACTIONS:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                action_attempted=action_str,
                payment_id=payment_id,
                event_id=event_id,
                executed=False,
                idempotency_key=idem_key,
                error=None,
                reason=(
                    f"Action '{action_str}' is not executable by the "
                    "recovery executor; requires manual handling"
                ),
                timestamp=now,
            )

        # --- Guard: unrecognized action (fail closed) ---
        if action not in _EXECUTABLE_ACTIONS:
            return ExecutionResult(
                status=ExecutionStatus.INVALID,
                action_attempted=action_str,
                payment_id=payment_id,
                event_id=event_id,
                executed=False,
                idempotency_key=idem_key,
                error=f"Unrecognized policy action: {action_str}",
                reason="Unknown action; failing closed for safety",
                timestamp=now,
            )

        # --- Guard: idempotency (in-memory, this process) ---
        if idem_key in self._executed_keys:
            prior = self._executed_keys[idem_key]
            return ExecutionResult(
                status=ExecutionStatus.DUPLICATE,
                action_attempted=action_str,
                payment_id=payment_id,
                event_id=event_id,
                executed=False,
                execution_id=prior.execution_id,
                idempotency_key=idem_key,
                error=None,
                reason=(
                    f"Duplicate execution prevented; action was already "
                    f"executed with execution_id={prior.execution_id}"
                ),
                timestamp=now,
            )

        # --- Guard: idempotency (durable, survives restarts) ---
        if self._state_store is not None:
            prior_row = self._state_store.get_execution(idem_key)
            if prior_row is not None:
                return ExecutionResult(
                    status=ExecutionStatus.DUPLICATE,
                    action_attempted=action_str,
                    payment_id=payment_id,
                    event_id=event_id,
                    executed=False,
                    execution_id=prior_row.get("execution_id"),
                    idempotency_key=idem_key,
                    error=None,
                    reason=(
                        "Duplicate execution prevented (durable ledger); action "
                        f"was already recorded with execution_id={prior_row.get('execution_id')}"
                    ),
                    timestamp=now,
                )

        # --- Execute via concrete implementation ---
        execution_id = str(uuid.uuid4())
        try:
            success, exec_error = self._do_execute(
                payment_event=payment_event,
                policy_decision=policy_decision,
                execution_id=execution_id,
            )
        except Exception as exc:
            logger.warning(
                "Execution failed for %s/%s: %s",
                event_id,
                action_str,
                exc,
            )
            result = ExecutionResult(
                status=ExecutionStatus.FAILED,
                action_attempted=action_str,
                payment_id=payment_id,
                event_id=event_id,
                executed=False,
                execution_id=execution_id,
                idempotency_key=idem_key,
                error=f"Execution exception: {exc}",
                reason="The recovery action raised an unexpected error",
                timestamp=now,
            )
            # Record even failed attempts to prevent re-execution
            self._executed_keys[idem_key] = result
            self._persist(result)
            return result

        if success:
            result = ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                action_attempted=action_str,
                payment_id=payment_id,
                event_id=event_id,
                executed=True,
                execution_id=execution_id,
                idempotency_key=idem_key,
                error=None,
                reason=f"Successfully executed {action_str} for {payment_id}",
                timestamp=now,
            )
        else:
            result = ExecutionResult(
                status=ExecutionStatus.FAILED,
                action_attempted=action_str,
                payment_id=payment_id,
                event_id=event_id,
                executed=False,
                execution_id=execution_id,
                idempotency_key=idem_key,
                error=exec_error or "Execution returned failure",
                reason=f"Execution of {action_str} failed for {payment_id}",
                timestamp=now,
            )

        # Record for idempotency (in-memory + durable)
        self._executed_keys[idem_key] = result
        self._persist(result)
        return result

    def _persist(self, result: ExecutionResult) -> None:
        """Best-effort durable record of an execution attempt.

        Writes the idempotency outcome and one attempt-history row. Never
        raises — a store failure must not crash the pipeline.
        """
        if self._state_store is None:
            return
        try:
            self._state_store.record_execution(
                idempotency_key=result.idempotency_key,
                payment_id=result.payment_id,
                event_id=result.event_id,
                action=result.action_attempted,
                status=result.status.value,
                execution_id=result.execution_id,
                executed=result.executed,
            )
            self._state_store.record_attempt(
                payment_id=result.payment_id,
                event_id=result.event_id,
                action=result.action_attempted,
                status=result.status.value,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Durable execution record failed: %s", exc)

    @abc.abstractmethod
    def _do_execute(
        self,
        payment_event: FailedTransactionEvent,
        policy_decision: PolicyDecision,
        execution_id: str,
    ) -> tuple[bool, str | None]:
        """Perform the concrete execution action.

        Subclasses implement this method.  The base class handles all
        policy-gate checks and idempotency before calling this.

        Args:
            payment_event: The failed payment (read-only).
            policy_decision: The authoritative policy decision.
            execution_id: Unique ID for this execution attempt.

        Returns:
            A tuple of (success: bool, error_message: str | None).
            Return (True, None) on success, (False, "reason") on failure.
        """
        ...
