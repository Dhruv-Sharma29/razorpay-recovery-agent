"""Tests for the deterministic escalation handler (TASK-007 Part A).

No network access, no Razorpay credentials, no NIM access required.

Coverage:
  1. policy denial → escalation
  2. manual review → escalation
  3. unknown/unsafe condition → escalation
  4. executor failure → escalation
  5. reasoning failure → safe escalation
  6. escalation never executes a payment
  7. escalation result contains required fields
  8. escalation behavior is deterministic
  9. malformed input fails closed
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from unittest.mock import patch

from app.classifier.result import ClassificationCertainty, ClassificationResult
from app.escalation.handler import EscalationHandler
from app.escalation.result import (
    EscalationResult,
    EscalationSeverity,
    EscalationStatus,
    EscalationTrigger,
)
from app.executor.mock import MockExecutor
from app.executor.result import ExecutionResult, ExecutionStatus
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.policy.result import EscalationReason, PolicyAction, PolicyDecision
from app.reasoning.result import ReasoningResult

import pytest


def _make_event(
    event_id: str = "evt_esc_001",
    payment_id: str = "pay_esc_001",
    amount: int = 149900,
    attempt: int = 1,
    category: FailureCategory = FailureCategory.INSUFFICIENT_FUNDS,
) -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id=event_id,
        razorpay_payment_id=payment_id,
        merchant_id="merch_01",
        customer_id="cust_01",
        type=TransactionType.ONE_TIME,
        amount=amount,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code="INSUFFICIENT_FUNDS",
        error_description="Payment failed due to insufficient funds",
        failure_category=category,
        attempt_number=attempt,
        mandate_status=None,
        timestamp=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
    )


def _make_classification(
    category: FailureCategory = FailureCategory.INSUFFICIENT_FUNDS,
) -> ClassificationResult:
    return ClassificationResult(
        category=category,
        confidence=1.0 if category != FailureCategory.UNKNOWN else 0.2,
        certainty=(
            ClassificationCertainty.HIGH
            if category != FailureCategory.UNKNOWN
            else ClassificationCertainty.LOW
        ),
        reason="test classification",
        rule_id="code.test",
        source_field="error_code",
    )


def _make_allowed_decision() -> PolicyDecision:
    return PolicyDecision(
        action=PolicyAction.SCHEDULED_RETRY,
        automatic_recovery_allowed=True,
        reason="Insufficient funds: retry after 24h cooldown permitted (attempt 1/2)",
        rule_id="policy.insufficient_funds.retry_24h",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        escalation_required=False,
        escalation_reason=None,
        max_retries_for_category=2,
        current_attempt=1,
        amount=149900,
        amount_limit=500000,
    )


def _make_denied_decision(
    *,
    reason: str = "Retry limit exhausted",
    escalation_reason: EscalationReason = EscalationReason.RETRY_LIMIT_EXHAUSTED,
    escalation_required: bool = True,
    category: FailureCategory = FailureCategory.INSUFFICIENT_FUNDS,
    action: PolicyAction = PolicyAction.ESCALATE,
) -> PolicyDecision:
    return PolicyDecision(
        action=action,
        automatic_recovery_allowed=False,
        reason=reason,
        rule_id="policy.test.denied",
        failure_category=category,
        escalation_required=escalation_required,
        escalation_reason=escalation_reason,
        max_retries_for_category=2,
        current_attempt=3,
        amount=149900,
        amount_limit=500000,
    )


def _make_reasoning(
    *,
    success: bool = True,
    policy_action_allowed: bool = True,
    is_fallback: bool = False,
    error: str | None = None,
) -> ReasoningResult:
    return ReasoningResult(
        success=success,
        recommendation="Follow policy",
        explanation="Policy decision stands",
        confidence=0.8 if success else 0.0,
        model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning" if success else "fallback",
        policy_action_allowed=policy_action_allowed,
        is_fallback=is_fallback,
        error=error,
    )


def _make_execution(
    *,
    status: ExecutionStatus = ExecutionStatus.FAILED,
    executed: bool = False,
    error: str | None = "downstream timeout",
) -> ExecutionResult:
    return ExecutionResult(
        status=status,
        action_attempted="scheduled_retry",
        payment_id="pay_esc_001",
        event_id="evt_esc_001",
        executed=executed,
        execution_id="exec_1",
        idempotency_key="idem_1",
        error=error,
        reason="Execution failed",
        timestamp=datetime(2026, 9, 1, 10, 0, 5, tzinfo=timezone.utc),
    )


@pytest.fixture()
def handler() -> EscalationHandler:
    return EscalationHandler()


@pytest.fixture()
def event() -> FailedTransactionEvent:
    return _make_event()


class TestPolicyDenialEscalation:
    def test_policy_denial_opens_escalation(self, handler, event):
        policy = _make_denied_decision()
        result = handler.handle(event, policy, classification=_make_classification())
        assert result.status == EscalationStatus.OPEN
        assert result.trigger == EscalationTrigger.POLICY_DENIAL
        assert result.authorizes_recovery is False
        assert "denied" in result.reason.lower() or "Retry" in result.reason


class TestManualReviewEscalation:
    def test_amount_cap_requires_manual_review(self, handler, event):
        policy = _make_denied_decision(
            reason="Transaction amount exceeds automatic recovery limit",
            escalation_reason=EscalationReason.AMOUNT_EXCEEDS_LIMIT,
        )
        result = handler.handle(event, policy, classification=_make_classification())
        assert result.status == EscalationStatus.OPEN
        assert result.trigger == EscalationTrigger.MANUAL_REVIEW
        assert result.severity == EscalationSeverity.HIGH
        assert result.authorizes_recovery is False


class TestUnknownUnsafeEscalation:
    def test_unknown_classification_escalates(self, handler, event):
        policy = _make_denied_decision(
            reason="Unknown failure: no automatic action permitted",
            escalation_reason=EscalationReason.UNKNOWN_FAILURE,
            category=FailureCategory.UNKNOWN,
        )
        result = handler.handle(
            event,
            policy,
            classification=_make_classification(FailureCategory.UNKNOWN),
        )
        assert result.status == EscalationStatus.OPEN
        assert result.trigger == EscalationTrigger.UNKNOWN_UNSAFE
        assert result.authorizes_recovery is False

    def test_unsafe_policy_reason_escalates(self, handler, event):
        policy = _make_denied_decision(
            reason="Unsafe condition detected",
            escalation_reason=EscalationReason.UNSAFE_CONDITION,
        )
        result = handler.handle(event, policy, classification=_make_classification())
        assert result.status == EscalationStatus.OPEN
        assert result.trigger == EscalationTrigger.UNKNOWN_UNSAFE

    def test_missing_policy_is_unsafe(self, handler, event):
        result = handler.handle(event, None)
        assert result.status == EscalationStatus.OPEN
        assert result.trigger == EscalationTrigger.UNKNOWN_UNSAFE
        assert result.authorizes_recovery is False


class TestExecutorFailureEscalation:
    def test_executor_failure_escalates(self, handler, event):
        policy = _make_allowed_decision()
        execution = _make_execution(status=ExecutionStatus.FAILED)
        result = handler.handle(
            event,
            policy,
            classification=_make_classification(),
            execution=execution,
        )
        assert result.status == EscalationStatus.OPEN
        assert result.trigger == EscalationTrigger.EXECUTOR_FAILURE
        assert result.authorizes_recovery is False
        assert "downstream timeout" in result.reason


class TestReasoningFailureSafeEscalation:
    def test_reasoning_failure_does_not_authorize_recovery(self, handler, event):
        policy = _make_allowed_decision()
        reasoning = _make_reasoning(
            success=False,
            policy_action_allowed=False,
            is_fallback=True,
            error="NIM unavailable",
        )
        result = handler.handle(
            event,
            policy,
            classification=_make_classification(),
            reasoning=reasoning,
        )
        assert result.status == EscalationStatus.OPEN
        assert result.trigger == EscalationTrigger.REASONING_FAILURE
        assert result.authorizes_recovery is False
        assert policy.automatic_recovery_allowed is True
        assert policy.action == PolicyAction.SCHEDULED_RETRY

    def test_reasoning_fallback_does_not_block_authorized_policy(self, handler, event):
        policy = _make_allowed_decision()
        reasoning = _make_reasoning(
            success=False,
            policy_action_allowed=True,
            is_fallback=True,
            error="NIM unavailable",
        )
        result = handler.handle(
            event,
            policy,
            classification=_make_classification(),
            reasoning=reasoning,
        )
        assert result.status == EscalationStatus.NOT_REQUIRED
        assert result.authorizes_recovery is False
        assert policy.automatic_recovery_allowed is True


class TestEscalationNeverExecutesPayment:
    def test_handle_does_not_call_executor(self, handler, event):
        policy = _make_denied_decision()
        executor = MockExecutor()
        with patch.object(executor, "execute", wraps=executor.execute) as spy:
            handler.handle(event, policy, classification=_make_classification())
            spy.assert_not_called()
        assert executor.execution_log == []

    def test_no_razorpay_or_http_clients_used(self, handler, event):
        policy = _make_denied_decision()
        with (
            patch("httpx.post") as http_post,
            patch("httpx.get") as http_get,
        ):
            handler.handle(event, policy, classification=_make_classification())
            http_post.assert_not_called()
            http_get.assert_not_called()


class TestEscalationResultFields:
    def test_required_fields_present(self, handler, event):
        policy = _make_denied_decision()
        ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = handler.handle(
            event,
            policy,
            classification=_make_classification(),
            timestamp=ts,
        )
        assert isinstance(result, EscalationResult)
        assert result.status == EscalationStatus.OPEN
        assert result.reason
        assert result.payment_id == event.razorpay_payment_id
        assert result.event_id == event.event_id
        assert result.severity in EscalationSeverity
        assert result.recommended_next_step
        assert result.timestamp == ts
        assert result.authorizes_recovery is False
        assert result.escalation_id
        assert result.policy_action == policy.action.value
        assert result.policy_reason == policy.reason


class TestEscalationDeterminism:
    def test_same_inputs_same_result(self, handler, event):
        policy = _make_denied_decision()
        ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        first = handler.handle(
            event,
            policy,
            classification=_make_classification(),
            timestamp=ts,
        )
        second = handler.handle(
            event,
            policy,
            classification=_make_classification(),
            timestamp=ts,
        )
        assert first.model_dump() == second.model_dump()


class TestMalformedInputFailsClosed:
    def test_none_event_fails_closed(self, handler):
        result = handler.handle(None, _make_denied_decision())
        assert result.status == EscalationStatus.FAILED_CLOSED
        assert result.trigger == EscalationTrigger.MALFORMED_INPUT
        assert result.authorizes_recovery is False
        assert result.severity == EscalationSeverity.CRITICAL

    def test_dict_event_fails_closed(self, handler):
        result = handler.handle({"razorpay_payment_id": "pay_x"}, _make_allowed_decision())  # type: ignore[arg-type]
        assert result.status == EscalationStatus.FAILED_CLOSED
        assert result.authorizes_recovery is False

    def test_malformed_policy_fails_closed(self, handler, event):
        result = handler.handle(event, "not-a-policy")  # type: ignore[arg-type]
        assert result.status == EscalationStatus.FAILED_CLOSED
        assert result.authorizes_recovery is False

    def test_malformed_classification_fails_closed(self, handler, event):
        result = handler.handle(
            event,
            _make_allowed_decision(),
            classification="bad",  # type: ignore[arg-type]
        )
        assert result.status == EscalationStatus.FAILED_CLOSED
        assert result.authorizes_recovery is False


class TestEscalationDoesNotMutateInputs:
    def test_payment_event_and_policy_unchanged(self, handler, event):
        policy = _make_denied_decision()
        event_before = event.model_dump()
        policy_before = policy.model_dump()
        handler.handle(event, policy, classification=_make_classification())
        assert event.model_dump() == event_before
        assert policy.model_dump() == policy_before

    def test_deepcopy_matches_after_handle(self, handler, event):
        original = copy.deepcopy(event)
        handler.handle(event, _make_allowed_decision())
        assert event.model_dump() == original.model_dump()


class TestAuthorizedPathNotEscalated:
    def test_allowed_policy_without_failures_is_not_required(self, handler, event):
        result = handler.handle(
            event,
            _make_allowed_decision(),
            classification=_make_classification(),
            reasoning=_make_reasoning(),
            execution=_make_execution(
                status=ExecutionStatus.SUCCESS,
                executed=True,
                error=None,
            ),
        )
        assert result.status == EscalationStatus.NOT_REQUIRED
        assert result.authorizes_recovery is False
        assert result.trigger is None
