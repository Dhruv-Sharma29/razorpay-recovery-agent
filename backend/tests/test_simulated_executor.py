"""Tests for the SimulatedPaymentExecutor's payment reporting.

The executor must prove money moved (or did not) so the batch metrics can
report measured recovery rather than a bare success count. It must also be
honest that no real gateway was contacted.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.executor import (
    ExecutionStatus,
    MockExecutor,
    SimulatedPaymentExecutor,
)
from app.classifier.result import FailureCategory
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyAction, PolicyDecision

AMOUNT = 149900


@pytest.fixture
def event() -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id="evt_sim_001",
        razorpay_payment_id="pay_sim_001",
        merchant_id="merch_01",
        customer_id="cust_001",
        type="one_time",
        amount=AMOUNT,
        currency="INR",
        payment_method="upi",
        error_code="INSUFFICIENT_FUNDS",
        error_description="Insufficient funds",
        failure_category="insufficient_funds",
        attempt_number=1,
        mandate_status=None,
        timestamp=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def allowed() -> PolicyDecision:
    return PolicyDecision(
        action=PolicyAction.SCHEDULED_RETRY,
        automatic_recovery_allowed=True,
        reason="Within retry limits",
        rule_id="policy.insufficient_funds",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        escalation_required=False,
        escalation_reason=None,
        max_retries_for_category=2,
        current_attempt=1,
        amount=AMOUNT,
        amount_limit=500000,
    )


class TestSimulatedPaymentResult:
    def test_success_reports_captured_and_full_amount(self, event, allowed):
        result = SimulatedPaymentExecutor().execute(event, allowed)

        assert result.status is ExecutionStatus.SUCCESS
        assert result.payment_status == "captured"
        assert result.amount_recovered == AMOUNT

    def test_failure_reports_failed_and_recovers_nothing(self, event, allowed):
        executor = SimulatedPaymentExecutor(simulate_failure=True)
        result = executor.execute(event, allowed)

        assert result.status is ExecutionStatus.FAILED
        assert result.payment_status == "failed"
        assert result.amount_recovered == 0

    def test_simulated_flag_is_always_true(self, event, allowed):
        ok = SimulatedPaymentExecutor().execute(event, allowed)
        failed = SimulatedPaymentExecutor(simulate_failure=True).execute(
            event, allowed
        )

        assert ok.simulated is True
        assert failed.simulated is True

    def test_rejected_action_recovers_nothing(self, event):
        denied = PolicyDecision(
            action=PolicyAction.ESCALATE,
            automatic_recovery_allowed=False,
            reason="Unknown failure",
            rule_id="policy.escalate",
            failure_category=FailureCategory.UNKNOWN,
            escalation_required=True,
            escalation_reason=None,
            max_retries_for_category=0,
            current_attempt=1,
            amount=AMOUNT,
            amount_limit=500000,
        )
        result = SimulatedPaymentExecutor().execute(event, denied)

        assert result.executed is False
        # Nothing was attempted, so nothing can have been recovered.
        assert not result.amount_recovered

    def test_mock_executor_alias_preserved(self):
        """Existing imports must keep working after the rename."""
        assert MockExecutor is SimulatedPaymentExecutor
