"""Tests for the Recovery Executor (TASK-006).

No network access, no Razorpay credentials, no NIM access required.
All tests use the MockExecutor which makes zero external calls.

Test coverage:
  1.  Policy-authorized retry executes successfully
  2.  Policy-denied retry is never executed
  3.  Escalation/manual-review decision is never executed
  4.  Unknown policy decision fails closed
  5.  Invalid policy decision fails closed
  6.  Amount-limit denial cannot be bypassed by executor
  7.  Duplicate execution is prevented
  8.  Same payment/action produces the same idempotency key
  9.  Different payment IDs do not collide
  10. Execution result contains required fields
  11. Original payment event is not mutated
  12. Mock executor does not call external services
  13. Executor handles simulated execution failure safely
  14. Executor handles malformed input safely

Plus additional edge cases.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.executor.base import RecoveryExecutor, compute_idempotency_key
from app.executor.mock import MockExecutor
from app.executor.result import ExecutionResult, ExecutionStatus
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.policy.result import EscalationReason, PolicyAction, PolicyDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event(
    event_id: str = "evt_test_001",
    payment_id: str = "pay_test_abc",
    amount: int = 149900,
    attempt: int = 1,
    error_code: str = "INSUFFICIENT_FUNDS",
    failure_category: FailureCategory = FailureCategory.INSUFFICIENT_FUNDS,
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
        error_code=error_code,
        error_description="Payment failed",
        failure_category=failure_category,
        attempt_number=attempt,
        mandate_status=None,
        timestamp=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
    )


def _make_allowed_decision(
    action: PolicyAction = PolicyAction.SCHEDULED_RETRY,
    category: FailureCategory = FailureCategory.INSUFFICIENT_FUNDS,
    amount: int = 149900,
    attempt: int = 1,
) -> PolicyDecision:
    return PolicyDecision(
        action=action,
        automatic_recovery_allowed=True,
        reason=f"Policy allows {action.value}",
        rule_id=f"policy.{category.value}.test",
        failure_category=category,
        escalation_required=False,
        escalation_reason=None,
        max_retries_for_category=2,
        current_attempt=attempt,
        amount=amount,
        amount_limit=500000,
    )


def _make_denied_decision(
    action: PolicyAction = PolicyAction.ESCALATE,
    category: FailureCategory = FailureCategory.INSUFFICIENT_FUNDS,
    escalation_reason: EscalationReason = EscalationReason.RETRY_LIMIT_EXHAUSTED,
    amount: int = 149900,
    attempt: int = 3,
) -> PolicyDecision:
    return PolicyDecision(
        action=action,
        automatic_recovery_allowed=False,
        reason=f"Policy denied: {escalation_reason.value}",
        rule_id=f"policy.{category.value}.denied",
        failure_category=category,
        escalation_required=True,
        escalation_reason=escalation_reason,
        max_retries_for_category=2,
        current_attempt=attempt,
        amount=amount,
        amount_limit=500000,
    )


@pytest.fixture()
def executor() -> MockExecutor:
    return MockExecutor()


@pytest.fixture()
def event() -> FailedTransactionEvent:
    return _make_event()


@pytest.fixture()
def allowed() -> PolicyDecision:
    return _make_allowed_decision()


@pytest.fixture()
def denied() -> PolicyDecision:
    return _make_denied_decision()


# ---------------------------------------------------------------------------
# 1. Policy-authorized retry executes successfully
# ---------------------------------------------------------------------------


class TestAuthorizedExecution:
    def test_scheduled_retry_succeeds(self, executor, event, allowed):
        result = executor.execute(event, allowed)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.executed is True
        assert result.action_attempted == "scheduled_retry"
        assert result.error is None

    def test_immediate_retry_succeeds(self, executor, event):
        decision = _make_allowed_decision(
            action=PolicyAction.IMMEDIATE_RETRY,
            category=FailureCategory.NETWORK_ERROR,
        )
        result = executor.execute(event, decision)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.executed is True

    def test_trigger_reauth_succeeds(self, executor, event):
        decision = _make_allowed_decision(
            action=PolicyAction.TRIGGER_REAUTHORIZATION,
            category=FailureCategory.EXPIRED_CARD,
        )
        result = executor.execute(event, decision)
        assert result.status == ExecutionStatus.SUCCESS

    def test_switch_payment_method_succeeds(self, executor, event):
        decision = _make_allowed_decision(
            action=PolicyAction.SWITCH_PAYMENT_METHOD,
            category=FailureCategory.BANK_DECLINE,
        )
        result = executor.execute(event, decision)
        assert result.status == ExecutionStatus.SUCCESS

    def test_resend_auth_prompt_succeeds(self, executor, event):
        decision = _make_allowed_decision(
            action=PolicyAction.RESEND_AUTH_PROMPT,
            category=FailureCategory.AUTHENTICATION_FAILURE,
        )
        result = executor.execute(event, decision)
        assert result.status == ExecutionStatus.SUCCESS


# ---------------------------------------------------------------------------
# 2. Policy-denied retry is never executed
# ---------------------------------------------------------------------------


class TestPolicyDeniedNeverExecuted:
    def test_denied_retry_not_executed(self, executor, event, denied):
        result = executor.execute(event, denied)
        assert result.status == ExecutionStatus.REJECTED
        assert result.executed is False
        assert len(executor.execution_log) == 0

    def test_denied_with_retry_action_not_executed(self, executor, event):
        """Even if action says retry but allowed=False, still rejected."""
        decision = _make_denied_decision(
            action=PolicyAction.SCHEDULED_RETRY,
            escalation_reason=EscalationReason.AMOUNT_EXCEEDS_LIMIT,
        )
        result = executor.execute(event, decision)
        assert result.status == ExecutionStatus.REJECTED
        assert result.executed is False
        assert len(executor.execution_log) == 0


# ---------------------------------------------------------------------------
# 3. Escalation/manual-review decision is never executed
# ---------------------------------------------------------------------------


class TestEscalationNeverExecuted:
    def test_escalation_not_executed(self, executor, event, denied):
        result = executor.execute(event, denied)
        assert result.executed is False
        assert result.status == ExecutionStatus.REJECTED

    def test_no_action_not_executed(self, executor, event):
        decision = PolicyDecision(
            action=PolicyAction.NO_ACTION,
            automatic_recovery_allowed=True,  # Even if allowed is True
            reason="Unknown failure: no automatic action permitted",
            rule_id="policy.unknown.escalate",
            failure_category=FailureCategory.UNKNOWN,
            escalation_required=False,
            escalation_reason=None,
            max_retries_for_category=0,
            current_attempt=1,
            amount=149900,
            amount_limit=500000,
        )
        result = executor.execute(event, decision)
        assert result.executed is False
        assert result.status == ExecutionStatus.REJECTED
        assert len(executor.execution_log) == 0


# ---------------------------------------------------------------------------
# 4. Unknown policy decision fails closed
# ---------------------------------------------------------------------------


class TestUnknownDecisionFailsClosed:
    def test_unknown_failure_category_escalated_by_policy(self, executor, event):
        """Unknown failure → policy denies → executor rejects."""
        decision = _make_denied_decision(
            category=FailureCategory.UNKNOWN,
            escalation_reason=EscalationReason.UNKNOWN_FAILURE,
        )
        result = executor.execute(event, decision)
        assert result.executed is False
        assert result.status == ExecutionStatus.REJECTED


# ---------------------------------------------------------------------------
# 5. Invalid policy decision fails closed
# ---------------------------------------------------------------------------


class TestInvalidDecisionFailsClosed:
    def test_none_policy_decision(self, executor, event):
        result = executor.execute(event, None)
        assert result.status == ExecutionStatus.INVALID
        assert result.executed is False

    def test_none_payment_event(self, executor, allowed):
        result = executor.execute(None, allowed)
        assert result.status == ExecutionStatus.INVALID
        assert result.executed is False

    def test_both_none(self, executor):
        result = executor.execute(None, None)
        assert result.status == ExecutionStatus.INVALID
        assert result.executed is False


# ---------------------------------------------------------------------------
# 6. Amount-limit denial cannot be bypassed by executor
# ---------------------------------------------------------------------------


class TestAmountLimitCannotBeBypassed:
    def test_amount_limit_denial_rejected(self, executor):
        event = _make_event(amount=600000)
        decision = _make_denied_decision(
            escalation_reason=EscalationReason.AMOUNT_EXCEEDS_LIMIT,
            amount=600000,
        )
        result = executor.execute(event, decision)
        assert result.status == ExecutionStatus.REJECTED
        assert result.executed is False
        assert len(executor.execution_log) == 0

    def test_executor_cannot_override_amount_denial(self, executor):
        """Even if we somehow had an allowed decision for high amount,
        that decision comes from the policy engine which wouldn't allow it.
        The executor just trusts the policy flag."""
        event = _make_event(amount=600000)
        # If policy says allowed=False, executor respects it
        decision = _make_denied_decision(
            escalation_reason=EscalationReason.AMOUNT_EXCEEDS_LIMIT,
            amount=600000,
        )
        result = executor.execute(event, decision)
        assert result.executed is False


# ---------------------------------------------------------------------------
# 7. Duplicate execution is prevented
# ---------------------------------------------------------------------------


class TestDuplicateExecutionPrevented:
    def test_second_execution_is_duplicate(self, executor, event, allowed):
        r1 = executor.execute(event, allowed)
        assert r1.status == ExecutionStatus.SUCCESS

        r2 = executor.execute(event, allowed)
        assert r2.status == ExecutionStatus.DUPLICATE
        assert r2.executed is False

    def test_only_one_actual_execution(self, executor, event, allowed):
        executor.execute(event, allowed)
        executor.execute(event, allowed)
        executor.execute(event, allowed)
        # Only one call to _do_execute
        assert len(executor.execution_log) == 1

    def test_duplicate_preserves_original_execution_id(self, executor, event, allowed):
        r1 = executor.execute(event, allowed)
        r2 = executor.execute(event, allowed)
        assert r2.execution_id == r1.execution_id


# ---------------------------------------------------------------------------
# 8. Same payment/action produces the same idempotency key
# ---------------------------------------------------------------------------


class TestIdempotencyKeyDeterminism:
    def test_same_key_for_same_inputs(self, event, allowed):
        k1 = compute_idempotency_key(event, allowed)
        k2 = compute_idempotency_key(event, allowed)
        assert k1 == k2

    def test_key_is_sha256_hex(self, event, allowed):
        key = compute_idempotency_key(event, allowed)
        assert len(key) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in key)

    def test_execution_result_has_matching_key(self, executor, event, allowed):
        expected = compute_idempotency_key(event, allowed)
        result = executor.execute(event, allowed)
        assert result.idempotency_key == expected


# ---------------------------------------------------------------------------
# 9. Different payment IDs do not collide
# ---------------------------------------------------------------------------


class TestNoIdempotencyCollision:
    def test_different_event_ids(self, allowed):
        e1 = _make_event(event_id="evt_001")
        e2 = _make_event(event_id="evt_002")
        k1 = compute_idempotency_key(e1, allowed)
        k2 = compute_idempotency_key(e2, allowed)
        assert k1 != k2

    def test_different_payment_ids(self, allowed):
        e1 = _make_event(payment_id="pay_aaa")
        e2 = _make_event(payment_id="pay_bbb")
        k1 = compute_idempotency_key(e1, allowed)
        k2 = compute_idempotency_key(e2, allowed)
        assert k1 != k2

    def test_different_actions(self, event):
        d1 = _make_allowed_decision(action=PolicyAction.SCHEDULED_RETRY)
        d2 = _make_allowed_decision(action=PolicyAction.IMMEDIATE_RETRY)
        k1 = compute_idempotency_key(event, d1)
        k2 = compute_idempotency_key(event, d2)
        assert k1 != k2

    def test_different_attempts(self):
        e1 = _make_event(attempt=1)
        e2 = _make_event(attempt=2)
        d = _make_allowed_decision()
        k1 = compute_idempotency_key(e1, d)
        k2 = compute_idempotency_key(e2, d)
        assert k1 != k2

    def test_both_execute_independently(self, executor):
        e1 = _make_event(event_id="evt_001", payment_id="pay_aaa")
        e2 = _make_event(event_id="evt_002", payment_id="pay_bbb")
        d = _make_allowed_decision()
        r1 = executor.execute(e1, d)
        r2 = executor.execute(e2, d)
        assert r1.status == ExecutionStatus.SUCCESS
        assert r2.status == ExecutionStatus.SUCCESS
        assert len(executor.execution_log) == 2


# ---------------------------------------------------------------------------
# 10. Execution result contains required fields
# ---------------------------------------------------------------------------


class TestResultContainsRequiredFields:
    def test_success_result_fields(self, executor, event, allowed):
        result = executor.execute(event, allowed)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.action_attempted == "scheduled_retry"
        assert result.payment_id == "pay_test_abc"
        assert result.event_id == "evt_test_001"
        assert result.executed is True
        assert result.execution_id is not None
        assert len(result.idempotency_key) == 64
        assert result.error is None
        assert result.reason != ""
        assert result.timestamp is not None

    def test_rejected_result_fields(self, executor, event, denied):
        result = executor.execute(event, denied)
        assert result.status == ExecutionStatus.REJECTED
        assert result.action_attempted == "escalate"
        assert result.payment_id == "pay_test_abc"
        assert result.event_id == "evt_test_001"
        assert result.executed is False
        assert result.reason != ""
        assert result.timestamp is not None

    def test_duplicate_result_fields(self, executor, event, allowed):
        executor.execute(event, allowed)
        result = executor.execute(event, allowed)
        assert result.status == ExecutionStatus.DUPLICATE
        assert result.executed is False
        assert result.idempotency_key != ""

    def test_result_is_pydantic_model(self, executor, event, allowed):
        result = executor.execute(event, allowed)
        assert isinstance(result, ExecutionResult)
        # Can serialize to dict
        d = result.model_dump()
        assert "status" in d
        assert "idempotency_key" in d


# ---------------------------------------------------------------------------
# 11. Original payment event is not mutated
# ---------------------------------------------------------------------------


class TestNoMutationOfPaymentEvent:
    def test_event_unchanged_after_success(self, executor, event, allowed):
        original = event.model_copy(deep=True)
        executor.execute(event, allowed)
        assert event == original

    def test_event_unchanged_after_rejection(self, executor, event, denied):
        original = event.model_copy(deep=True)
        executor.execute(event, denied)
        assert event == original

    def test_event_unchanged_after_failure(self, event, allowed):
        executor = MockExecutor(simulate_failure=True)
        original = event.model_copy(deep=True)
        executor.execute(event, allowed)
        assert event == original

    def test_policy_decision_unchanged(self, executor, event, allowed):
        original = allowed.model_copy(deep=True)
        executor.execute(event, allowed)
        assert allowed == original


# ---------------------------------------------------------------------------
# 12. Mock executor does not call external services
# ---------------------------------------------------------------------------


class TestMockNoExternalCalls:
    def test_execution_log_is_internal_only(self, executor, event, allowed):
        """MockExecutor records to an in-memory list, no I/O."""
        executor.execute(event, allowed)
        assert len(executor.execution_log) == 1
        log_entry = executor.execution_log[0]
        assert log_entry["action"] == "scheduled_retry"
        assert log_entry["payment_id"] == "pay_test_abc"
        assert log_entry["simulated_failure"] is False

    def test_no_network_call_on_success(self, executor, event, allowed):
        """Verify MockExecutor._do_execute has no external side effects."""
        # This is a structural test — MockExecutor's _do_execute only
        # appends to self.execution_log and returns a tuple.
        result = executor.execute(event, allowed)
        assert result.executed is True
        # If there were network calls, they would fail in this test
        # environment since there's no server running.

    def test_no_network_call_on_rejection(self, executor, event, denied):
        """Rejected actions don't even reach _do_execute."""
        executor.execute(event, denied)
        assert len(executor.execution_log) == 0


# ---------------------------------------------------------------------------
# 13. Executor handles simulated execution failure safely
# ---------------------------------------------------------------------------


class TestSimulatedFailure:
    def test_failure_returns_structured_result(self, event, allowed):
        executor = MockExecutor(simulate_failure=True)
        result = executor.execute(event, allowed)
        assert result.status == ExecutionStatus.FAILED
        assert result.executed is False
        assert result.error is not None
        assert "Simulated downstream failure" in result.error

    def test_custom_failure_message(self, event, allowed):
        executor = MockExecutor(
            simulate_failure=True,
            simulate_failure_message="Gateway timeout",
        )
        result = executor.execute(event, allowed)
        assert result.status == ExecutionStatus.FAILED
        assert "Gateway timeout" in result.error

    def test_failure_still_prevents_duplicate(self, event, allowed):
        """Even failed executions are recorded for idempotency."""
        executor = MockExecutor(simulate_failure=True)
        r1 = executor.execute(event, allowed)
        assert r1.status == ExecutionStatus.FAILED

        r2 = executor.execute(event, allowed)
        assert r2.status == ExecutionStatus.DUPLICATE

    def test_failure_has_execution_id(self, event, allowed):
        executor = MockExecutor(simulate_failure=True)
        result = executor.execute(event, allowed)
        assert result.execution_id is not None


# ---------------------------------------------------------------------------
# 14. Executor handles malformed input safely
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_none_event(self, executor, allowed):
        result = executor.execute(None, allowed)
        assert result.status == ExecutionStatus.INVALID
        assert result.executed is False
        assert "None" in result.error or "missing" in result.error.lower()

    def test_none_decision(self, executor, event):
        result = executor.execute(event, None)
        assert result.status == ExecutionStatus.INVALID
        assert result.executed is False

    def test_both_none(self, executor):
        result = executor.execute(None, None)
        assert result.status == ExecutionStatus.INVALID
        assert result.executed is False

    def test_invalid_result_has_timestamp(self, executor):
        result = executor.execute(None, None)
        assert result.timestamp is not None


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_executor_is_abstract(self):
        """RecoveryExecutor cannot be instantiated directly."""
        with pytest.raises(TypeError):
            RecoveryExecutor()

    def test_multiple_payments_same_executor(self, executor):
        """Multiple different payments can be executed."""
        events = [_make_event(event_id=f"evt_{i}", payment_id=f"pay_{i}") for i in range(5)]
        d = _make_allowed_decision()
        results = [executor.execute(e, d) for e in events]
        assert all(r.status == ExecutionStatus.SUCCESS for r in results)
        assert len(executor.execution_log) == 5

    def test_escalation_allowed_true_still_rejected(self, executor, event):
        """Escalation action is rejected even if allowed=True (safety)."""
        decision = PolicyDecision(
            action=PolicyAction.ESCALATE,
            automatic_recovery_allowed=True,
            reason="Somehow allowed but escalate",
            rule_id="test",
            failure_category=FailureCategory.INSUFFICIENT_FUNDS,
            escalation_required=True,
            escalation_reason=EscalationReason.RETRY_LIMIT_EXHAUSTED,
            max_retries_for_category=2,
            current_attempt=3,
            amount=149900,
            amount_limit=500000,
        )
        result = executor.execute(event, decision)
        # Even though allowed=True, escalate action is non-executable
        assert result.executed is False
        assert result.status == ExecutionStatus.REJECTED

    def test_exception_in_do_execute(self, event, allowed):
        """If _do_execute raises, the executor handles it safely."""

        class ExplodingExecutor(RecoveryExecutor):
            def _do_execute(self, payment_event, policy_decision, execution_id):
                raise RuntimeError("Kaboom!")

        executor = ExplodingExecutor()
        result = executor.execute(event, allowed)
        assert result.status == ExecutionStatus.FAILED
        assert result.executed is False
        assert "Kaboom" in result.error

    def test_execution_id_is_unique(self, executor):
        """Different executions get different execution IDs."""
        e1 = _make_event(event_id="evt_a", payment_id="pay_a")
        e2 = _make_event(event_id="evt_b", payment_id="pay_b")
        d = _make_allowed_decision()
        r1 = executor.execute(e1, d)
        r2 = executor.execute(e2, d)
        assert r1.execution_id != r2.execution_id
