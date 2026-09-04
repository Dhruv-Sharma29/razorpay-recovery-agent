"""Deterministic escalation handler.

The policy engine remains the authority for recovery decisions.
This handler only flags cases that cannot be recovered automatically.

It NEVER:
- authorizes recovery
- overrides a policy decision
- executes a payment
- calls Razorpay
- calls NIM / Nemotron / external LLMs
- mutates the payment event or policy decision
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from app.classifier.result import ClassificationResult
from app.executor.result import ExecutionResult, ExecutionStatus
from app.escalation.result import (
    EscalationResult,
    EscalationSeverity,
    EscalationStatus,
    EscalationTrigger,
)
from app.models.payment_event import FailedTransactionEvent, FailureCategory
from app.policy.result import EscalationReason, PolicyDecision
from app.reasoning.result import ReasoningResult

logger = logging.getLogger(__name__)

_UNKNOWN_POLICY_REASONS = frozenset(
    {
        EscalationReason.UNKNOWN_FAILURE,
        EscalationReason.UNSAFE_CONDITION,
        EscalationReason.INVALID_CLASSIFICATION,
        EscalationReason.MISSING_CLASSIFICATION,
    }
)

_NEXT_STEPS: dict[EscalationTrigger, str] = {
    EscalationTrigger.POLICY_DENIAL: (
        "Do not retry automatically. Review the policy reason and handle manually."
    ),
    EscalationTrigger.MANUAL_REVIEW: (
        "Route to operations for manual review. Do not execute recovery."
    ),
    EscalationTrigger.UNKNOWN_UNSAFE: (
        "Investigate the unclassified or unsafe failure before any recovery attempt."
    ),
    EscalationTrigger.EXECUTOR_FAILURE: (
        "Inspect the executor error. Retry only after a new policy evaluation."
    ),
    EscalationTrigger.REASONING_FAILURE: (
        "Preserve the original policy decision. Do not treat reasoning failure as authorization."
    ),
    EscalationTrigger.MALFORMED_INPUT: (
        "Reject the event and do not execute recovery."
    ),
}

_NO_ESCALATION_STEP = (
    "No escalation required. Follow the existing policy decision; "
    "this handler does not authorize recovery."
)


def _escalation_id(payment_id: str, trigger: EscalationTrigger | None, reason: str) -> str:
    """Stable id so the same evaluation is deterministic."""
    trigger_value = trigger.value if trigger is not None else "none"
    raw = f"{payment_id}|{trigger_value}|{reason}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _payment_id_from_event(event: Any) -> str:
    if event is None:
        return "unknown"
    if isinstance(event, FailedTransactionEvent):
        return event.razorpay_payment_id or "unknown"
    payment_id = getattr(event, "razorpay_payment_id", None)
    if payment_id:
        return str(payment_id)
    return "unknown"


def _event_id_from_event(event: Any) -> str:
    if event is None:
        return "unknown"
    if isinstance(event, FailedTransactionEvent):
        return event.event_id or "unknown"
    event_id = getattr(event, "event_id", None)
    if event_id:
        return str(event_id)
    return "unknown"


class EscalationHandler:
    """Deterministic, fail-closed escalation component.

    Evaluates whether a payment needs human intervention. It never
    executes payments or grants recovery authority.
    """

    def handle(
        self,
        payment_event: FailedTransactionEvent | None,
        policy_decision: PolicyDecision | None,
        *,
        classification: ClassificationResult | None = None,
        reasoning: ReasoningResult | None = None,
        execution: ExecutionResult | None = None,
        timestamp: datetime | None = None,
    ) -> EscalationResult:
        """Evaluate escalation for a recovery-agent decision context.

        Returns a structured ``EscalationResult``. ``authorizes_recovery``
        is always False.
        """
        now = timestamp if timestamp is not None else datetime.now(timezone.utc)
        payment_id = _payment_id_from_event(payment_event)
        event_id = _event_id_from_event(payment_event)

        try:
            policy_action = (
                policy_decision.action.value
                if isinstance(policy_decision, PolicyDecision)
                else None
            )
            policy_reason = (
                policy_decision.reason
                if isinstance(policy_decision, PolicyDecision)
                else None
            )

            trigger, reason, severity, status = self._evaluate(
                payment_event=payment_event,
                policy_decision=policy_decision,
                classification=classification,
                reasoning=reasoning,
                execution=execution,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Escalation evaluation failed closed: %s", exc)
            reason = "Escalation input could not be evaluated; failing closed"
            trigger = EscalationTrigger.MALFORMED_INPUT
            status = EscalationStatus.FAILED_CLOSED
            severity = EscalationSeverity.CRITICAL
            policy_action = None
            policy_reason = None

        if status == EscalationStatus.NOT_REQUIRED:
            recommended = _NO_ESCALATION_STEP
        else:
            recommended = _NEXT_STEPS[trigger]  # type: ignore[index]

        result = EscalationResult(
            status=status,
            reason=reason,
            payment_id=payment_id,
            event_id=event_id,
            severity=severity,
            recommended_next_step=recommended,
            timestamp=now,
            trigger=trigger,
            escalation_id=_escalation_id(payment_id, trigger, reason),
            authorizes_recovery=False,
            policy_action=policy_action,
            policy_reason=policy_reason,
        )

        logger.info(
            "Escalation evaluated payment=%s status=%s trigger=%s",
            payment_id,
            status.value,
            trigger.value if trigger else "none",
        )
        return result

    def _evaluate(
        self,
        *,
        payment_event: FailedTransactionEvent | None,
        policy_decision: PolicyDecision | None,
        classification: ClassificationResult | None,
        reasoning: ReasoningResult | None,
        execution: ExecutionResult | None,
    ) -> tuple[
        EscalationTrigger | None,
        str,
        EscalationSeverity,
        EscalationStatus,
    ]:
        # --- Fail closed: malformed / missing core input ---
        if not isinstance(payment_event, FailedTransactionEvent):
            return (
                EscalationTrigger.MALFORMED_INPUT,
                "Payment event is missing or malformed; failing closed",
                EscalationSeverity.CRITICAL,
                EscalationStatus.FAILED_CLOSED,
            )

        if not payment_event.razorpay_payment_id or not payment_event.event_id:
            return (
                EscalationTrigger.MALFORMED_INPUT,
                "Payment event is missing required identifiers; failing closed",
                EscalationSeverity.CRITICAL,
                EscalationStatus.FAILED_CLOSED,
            )

        if policy_decision is not None and not isinstance(
            policy_decision, PolicyDecision
        ):
            return (
                EscalationTrigger.MALFORMED_INPUT,
                "Policy decision is malformed; failing closed",
                EscalationSeverity.CRITICAL,
                EscalationStatus.FAILED_CLOSED,
            )

        if policy_decision is None:
            return (
                EscalationTrigger.UNKNOWN_UNSAFE,
                "Policy decision is missing; cannot safely continue automatic recovery",
                EscalationSeverity.HIGH,
                EscalationStatus.OPEN,
            )

        if classification is not None and not isinstance(
            classification, ClassificationResult
        ):
            return (
                EscalationTrigger.MALFORMED_INPUT,
                "Classification result is malformed; failing closed",
                EscalationSeverity.CRITICAL,
                EscalationStatus.FAILED_CLOSED,
            )

        if reasoning is not None and not isinstance(reasoning, ReasoningResult):
            return (
                EscalationTrigger.MALFORMED_INPUT,
                "Reasoning result is malformed; failing closed",
                EscalationSeverity.CRITICAL,
                EscalationStatus.FAILED_CLOSED,
            )

        if execution is not None and not isinstance(execution, ExecutionResult):
            return (
                EscalationTrigger.MALFORMED_INPUT,
                "Execution result is malformed; failing closed",
                EscalationSeverity.CRITICAL,
                EscalationStatus.FAILED_CLOSED,
            )

        # --- Unknown / unsafe classification ---
        if classification is not None and classification.category == FailureCategory.UNKNOWN:
            return (
                EscalationTrigger.UNKNOWN_UNSAFE,
                (
                    "Failure classification is unknown or unsafe; "
                    f"{classification.reason}"
                ),
                EscalationSeverity.HIGH,
                EscalationStatus.OPEN,
            )

        if policy_decision.failure_category == FailureCategory.UNKNOWN:
            return (
                EscalationTrigger.UNKNOWN_UNSAFE,
                (
                    "Policy classified the failure as unknown; "
                    f"{policy_decision.reason}"
                ),
                EscalationSeverity.HIGH,
                EscalationStatus.OPEN,
            )

        if policy_decision.escalation_reason in _UNKNOWN_POLICY_REASONS:
            return (
                EscalationTrigger.UNKNOWN_UNSAFE,
                (
                    "Unsafe or unknown condition from policy: "
                    f"{policy_decision.escalation_reason.value}; "
                    f"{policy_decision.reason}"
                ),
                EscalationSeverity.HIGH,
                EscalationStatus.OPEN,
            )

        # --- Executor failure requiring human intervention ---
        if execution is not None and execution.status in (
            ExecutionStatus.FAILED,
            ExecutionStatus.INVALID,
        ):
            exec_error = execution.error or execution.reason
            return (
                EscalationTrigger.EXECUTOR_FAILURE,
                f"Executor requires human intervention: {exec_error}",
                EscalationSeverity.HIGH,
                EscalationStatus.OPEN,
            )

        # --- Policy requires manual review (e.g. amount cap) ---
        if policy_decision.escalation_reason == EscalationReason.AMOUNT_EXCEEDS_LIMIT:
            return (
                EscalationTrigger.MANUAL_REVIEW,
                f"Policy requires manual review: {policy_decision.reason}",
                EscalationSeverity.HIGH,
                EscalationStatus.OPEN,
            )

        if policy_decision.escalation_required and policy_decision.escalation_reason not in (
            EscalationReason.RETRY_LIMIT_EXHAUSTED,
            EscalationReason.GLOBAL_ATTEMPT_CAP,
            EscalationReason.NON_RETRYABLE_FAILURE,
        ):
            return (
                EscalationTrigger.MANUAL_REVIEW,
                f"Policy requires manual review: {policy_decision.reason}",
                EscalationSeverity.HIGH,
                EscalationStatus.OPEN,
            )

        # --- Policy denies automatic recovery ---
        if not policy_decision.automatic_recovery_allowed:
            return (
                EscalationTrigger.POLICY_DENIAL,
                f"Policy denied automatic recovery: {policy_decision.reason}",
                EscalationSeverity.MEDIUM,
                EscalationStatus.OPEN,
            )

        if policy_decision.escalation_required:
            return (
                EscalationTrigger.MANUAL_REVIEW,
                f"Policy requires manual review: {policy_decision.reason}",
                EscalationSeverity.HIGH,
                EscalationStatus.OPEN,
            )

        # --- Reasoning failure where safe continuation is impossible ---
        # A normal explanation fallback while policy still authorizes recovery
        # is not blocking. Escalation applies when the reasoning result does
        # not preserve authorization, so auto-continue would be unsafe.
        if (
            reasoning is not None
            and not reasoning.success
            and not reasoning.policy_action_allowed
        ):
            error = reasoning.error or "reasoning layer failed"
            return (
                EscalationTrigger.REASONING_FAILURE,
                (
                    "Reasoning layer failed and automatic continuation is not safe: "
                    f"{error}"
                ),
                EscalationSeverity.MEDIUM,
                EscalationStatus.OPEN,
            )

        return (
            None,
            "No escalation required; policy allows the prescribed recovery path",
            EscalationSeverity.LOW,
            EscalationStatus.NOT_REQUIRED,
        )
