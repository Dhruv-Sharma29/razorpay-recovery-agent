"""Pending/scheduled retry tests (P2).

The brief's flow is: failure -> retry scheduled for +24h -> pending ->
simulated retry runs -> payment succeeds -> recovered amount recorded.
A scheduled retry must never be reported as "recovered" before it runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.audit.result import AuditOutcome
from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.escalation.handler import EscalationHandler
from app.executor.mock import SimulatedPaymentExecutor
from app.executor.result import ExecutionStatus
from app.models.payment_event import FailedTransactionEvent
from app.persistence.store import RecoveryStateStore
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.reasoning.engine import RecoveryReasoner
from app.scheduler import run_due_jobs

AMOUNT = 149900
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def _event(event_id: str = "evt_sched_1") -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id=event_id,
        razorpay_payment_id=f"pay_{event_id}",
        merchant_id="merch_01",
        customer_id="cust_1",
        type="one_time",
        amount=AMOUNT,
        currency="INR",
        payment_method="upi",
        error_code="INSUFFICIENT_FUNDS",
        error_description="Insufficient funds",
        failure_category="insufficient_funds",
        attempt_number=1,
        mandate_status=None,
        timestamp=T0,
    )


def _pipeline(tmp_path, store=None):
    db = f"sqlite:///{tmp_path}/audit_{'sched' if store else 'plain'}.db"
    return RecoveryPipeline(
        classifier=FailureClassifier(),
        policy_engine=RecoveryPolicyEngine(),
        reasoner=RecoveryReasoner(),
        executor=SimulatedPaymentExecutor(),
        escalation_handler=EscalationHandler(),
        audit_logger=AuditLogger(database_url=db),
        state_store=store,
    )


@pytest.fixture
def store(tmp_path) -> RecoveryStateStore:
    return RecoveryStateStore(database_url=f"sqlite:///{tmp_path}/state.db")


class TestPolicyCooldown:
    def test_insufficient_funds_carries_a_24h_cooldown(self):
        decision = RecoveryPolicyEngine().evaluate(
            _event(), FailureClassifier().classify(_event())
        )
        assert decision.cooldown_seconds == 86_400

    def test_immediate_categories_have_no_cooldown(self):
        ev = _event("evt_net")
        ev = ev.model_copy(update={"error_code": "GATEWAY_ERROR"})
        decision = RecoveryPolicyEngine().evaluate(
            ev, FailureClassifier().classify(ev)
        )
        assert decision.cooldown_seconds == 0


class TestScheduling:
    def test_retry_is_scheduled_not_executed(self, tmp_path, store):
        result = _pipeline(tmp_path, store).process(_event())

        assert result.execution.status is ExecutionStatus.SCHEDULED
        assert result.execution.executed is False
        # Crucially: no money is claimed before the retry runs.
        assert result.execution.amount_recovered == 0
        assert result.execution.payment_status == "not_attempted"

    def test_scheduled_outcome_is_pending_not_recovered(self, tmp_path, store):
        result = _pipeline(tmp_path, store).process(_event())
        assert result.final_outcome is AuditOutcome.PENDING
        assert result.final_outcome is not AuditOutcome.RECOVERED

    def test_a_pending_job_is_persisted(self, tmp_path, store):
        _pipeline(tmp_path, store).process(_event())
        jobs = store.list_jobs(status="pending")

        assert len(jobs) == 1
        assert jobs[0]["payment_id"] == "pay_evt_sched_1"
        assert jobs[0]["action"] == "scheduled_retry"

    def test_cooldown_sets_eligibility_24h_out(self, tmp_path, store):
        _pipeline(tmp_path, store).process(_event())
        job = store.list_jobs(status="pending")[0]

        eligible = datetime.fromisoformat(job["next_eligible_at"])
        assert eligible - datetime.now(timezone.utc) > timedelta(hours=23)

    def test_without_a_store_execution_is_immediate(self, tmp_path):
        """Backward compatible: no store means the old inline behaviour."""
        result = _pipeline(tmp_path, store=None).process(_event())
        assert result.execution.status is ExecutionStatus.SUCCESS


class TestWorker:
    def test_no_op_before_the_cooldown_elapses(self, tmp_path, store):
        _pipeline(tmp_path, store).process(_event())
        runner = _pipeline(tmp_path)  # no store: actually executes

        report = run_due_jobs(store, runner, now=datetime.now(timezone.utc))

        assert report.ran == 0
        assert store.list_jobs(status="pending")
    def test_recovers_with_amount_once_eligible(self, tmp_path, store):
        _pipeline(tmp_path, store).process(_event())
        runner = _pipeline(tmp_path)
        later = datetime.now(timezone.utc) + timedelta(hours=25)

        report = run_due_jobs(store, runner, now=later)

        assert report.ran == 1
        assert report.recovered == 1
        assert report.amount_recovered == AMOUNT

    def test_job_leaves_pending_after_running(self, tmp_path, store):
        _pipeline(tmp_path, store).process(_event())
        runner = _pipeline(tmp_path)
        later = datetime.now(timezone.utc) + timedelta(hours=25)

        run_due_jobs(store, runner, now=later)

        assert store.list_jobs(status="pending") == []
        assert len(store.list_jobs(status="done")) == 1

    def test_worker_is_idempotent(self, tmp_path, store):
        """A second pass must not recover the same money twice."""
        _pipeline(tmp_path, store).process(_event())
        runner = _pipeline(tmp_path)
        later = datetime.now(timezone.utc) + timedelta(hours=25)

        first = run_due_jobs(store, runner, now=later)
        second = run_due_jobs(store, runner, now=later)

        assert first.recovered == 1
        assert second.ran == 0
        assert second.amount_recovered == 0

    def test_scheduling_the_same_work_twice_makes_one_job(self, tmp_path, store):
        pipeline = _pipeline(tmp_path, store)
        pipeline.process(_event())
        pipeline.process(_event())

        assert len(store.list_jobs(status="pending")) == 1
