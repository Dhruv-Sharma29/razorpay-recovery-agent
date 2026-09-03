"""Phase A: durable recovery-state store + persistent executor idempotency."""

from __future__ import annotations

from datetime import datetime, timezone

from app.classifier.engine import FailureClassifier
from app.executor.mock import MockExecutor
from app.executor.result import ExecutionStatus
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.persistence.store import RecoveryStateStore
from app.policy.engine import RecoveryPolicyEngine


def _event() -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id="evt_p1",
        razorpay_payment_id="pay_p1",
        merchant_id="m",
        customer_id="c",
        type=TransactionType.ONE_TIME,
        amount=149900,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code="INSUFFICIENT_FUNDS",
        error_description="insufficient balance",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        attempt_number=1,
        mandate_status=None,
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def _url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'state.db'}"


class TestStateStore:
    def test_ledger_persists_across_instances(self, tmp_path):
        url = _url(tmp_path)
        s1 = RecoveryStateStore(url)
        s1.record_execution(
            idempotency_key="k1", payment_id="pay_p1", event_id="evt_p1",
            action="scheduled_retry", status="success", execution_id="ex1", executed=True,
        )
        s1.close()

        s2 = RecoveryStateStore(url)  # simulates a restart
        row = s2.get_execution("k1")
        assert row is not None
        assert row["execution_id"] == "ex1"
        assert row["executed"] is True

    def test_attempt_count_and_next(self, tmp_path):
        store = RecoveryStateStore(_url(tmp_path))
        for _ in range(3):
            store.record_attempt(payment_id="pay_p1", event_id="evt_p1",
                                 action="scheduled_retry", status="success")
        assert store.count_attempts("pay_p1") == 3
        assert store.next_attempt_number("pay_p1") == 4
        assert store.count_attempts("pay_other") == 0


class TestDurableExecutorIdempotency:
    def test_duplicate_prevented_across_restart(self, tmp_path):
        url = _url(tmp_path)
        event = _event()
        decision = RecoveryPolicyEngine().evaluate(
            event, FailureClassifier().classify(event)
        )
        assert decision.automatic_recovery_allowed is True

        # First executor executes the action.
        store_a = RecoveryStateStore(url)
        result1 = MockExecutor(state_store=store_a).execute(event, decision)
        assert result1.status == ExecutionStatus.SUCCESS
        store_a.close()

        # A fresh executor + fresh store on the SAME db (simulated restart)
        # must refuse to execute the same action again.
        store_b = RecoveryStateStore(url)
        result2 = MockExecutor(state_store=store_b).execute(event, decision)
        assert result2.status == ExecutionStatus.DUPLICATE
        assert result2.executed is False

    def test_no_store_still_works_in_memory(self):
        event = _event()
        decision = RecoveryPolicyEngine().evaluate(
            event, FailureClassifier().classify(event)
        )
        ex = MockExecutor()  # no durable store
        assert ex.execute(event, decision).status == ExecutionStatus.SUCCESS
        # same instance still dedupes in-memory
        assert ex.execute(event, decision).status == ExecutionStatus.DUPLICATE
