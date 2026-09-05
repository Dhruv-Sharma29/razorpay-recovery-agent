"""Audit decision-chain tests (P4).

Every record must carry the full chain on its own — cause, rule, bound,
action, outcome, recovered amount — so a reviewer can audit one row
without replaying the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.escalation.handler import EscalationHandler
from app.executor.mock import SimulatedPaymentExecutor
from app.models.payment_event import FailedTransactionEvent
from app.persistence.store import RecoveryStateStore
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.reasoning.engine import RecoveryReasoner
from app.scheduler import run_due_jobs

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def _event(event_id="evt_chain_1", error_code="GATEWAY_ERROR", amount=149900):
    return FailedTransactionEvent(
        event_id=event_id,
        razorpay_payment_id=f"pay_{event_id}",
        merchant_id="merch_01",
        customer_id="cust_1",
        type="one_time",
        amount=amount,
        currency="INR",
        payment_method="upi",
        error_code=error_code,
        error_description="failure",
        failure_category="unknown",
        attempt_number=1,
        mandate_status=None,
        timestamp=T0,
    )


def _pipeline(tmp_path, store=None, tag="a"):
    return RecoveryPipeline(
        classifier=FailureClassifier(),
        policy_engine=RecoveryPolicyEngine(),
        reasoner=RecoveryReasoner(),
        executor=SimulatedPaymentExecutor(),
        escalation_handler=EscalationHandler(),
        audit_logger=AuditLogger(database_url=f"sqlite:///{tmp_path}/audit_{tag}.db"),
        state_store=store,
    )


@pytest.fixture
def immediate_record(tmp_path):
    """A network error executes inline, so money moves immediately."""
    result = _pipeline(tmp_path).process(_event())
    return result.audit_write.record


class TestChainOnAnExecutedRecovery:
    def test_cause_is_recorded_with_its_rule(self, immediate_record):
        assert immediate_record.classification_category == "network_error"
        assert immediate_record.classification_rule_id

    def test_action_is_recorded_with_its_rule(self, immediate_record):
        assert immediate_record.policy_action == "immediate_retry"
        assert immediate_record.policy_rule_id

    def test_bounds_are_recorded(self, immediate_record):
        assert immediate_record.amount_limit == 500000
        assert immediate_record.max_retries == 1
        assert immediate_record.cooldown_seconds == 0

    def test_recovered_amount_and_status_are_recorded(self, immediate_record):
        assert immediate_record.payment_status == "captured"
        assert immediate_record.amount_recovered == 149900

    def test_outcome_is_recorded(self, immediate_record):
        assert immediate_record.final_outcome.value == "recovered"

    def test_reasoning_fallback_is_labelled(self, immediate_record):
        # reasoning_is_fallback is True when no NIM key is configured,
        # False when the model actually responds.  Both are valid; the
        # important invariant is that the field is always populated.
        assert isinstance(immediate_record.reasoning_is_fallback, bool)


class TestChainOnADeferredRetry:
    def test_scheduled_record_shows_the_bound_and_no_money(self, tmp_path):
        store = RecoveryStateStore(database_url=f"sqlite:///{tmp_path}/state.db")
        result = _pipeline(tmp_path, store, tag="sched").process(
            _event("evt_chain_sched", error_code="INSUFFICIENT_FUNDS")
        )
        record = result.audit_write.record

        assert record.cooldown_seconds == 86_400
        assert record.payment_status == "not_attempted"
        assert record.amount_recovered == 0
        assert record.final_outcome.value == "pending"

    def test_scheduled_for_timestamp_is_captured(self, tmp_path):
        store = RecoveryStateStore(database_url=f"sqlite:///{tmp_path}/state2.db")
        result = _pipeline(tmp_path, store, tag="sched2").process(
            _event("evt_chain_sched2", error_code="INSUFFICIENT_FUNDS")
        )
        record = result.audit_write.record

        assert record.scheduled_for
        assert datetime.fromisoformat(record.scheduled_for) > datetime.now(
            timezone.utc
        ) + timedelta(hours=23)


class TestChainOnAnEscalation:
    def test_escalation_trigger_is_recorded(self, tmp_path):
        # Over the amount cap, so the policy refuses and escalates.
        result = _pipeline(tmp_path, tag="esc").process(
            _event("evt_chain_esc", error_code="INSUFFICIENT_FUNDS", amount=9_000_000)
        )
        record = result.audit_write.record

        assert record.final_outcome.value == "escalated"
        assert record.escalation_trigger
        assert record.amount_limit == 500000

    def test_escalated_record_recovers_nothing(self, tmp_path):
        result = _pipeline(tmp_path, tag="esc2").process(
            _event("evt_chain_esc2", error_code="INSUFFICIENT_FUNDS", amount=9_000_000)
        )
        assert not result.audit_write.record.amount_recovered


class TestChainSurvivesRoundTrip:
    def test_chain_is_readable_back_from_storage(self, tmp_path):
        logger = AuditLogger(database_url=f"sqlite:///{tmp_path}/audit_rt.db")
        pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            reasoner=RecoveryReasoner(),
            executor=SimulatedPaymentExecutor(),
            escalation_handler=EscalationHandler(),
            audit_logger=logger,
        )
        pipeline.process(_event("evt_chain_rt"))

        stored = logger.list_records()[-1]
        assert stored.classification_rule_id
        assert stored.policy_rule_id
        assert stored.amount_recovered == 149900
        assert stored.payment_status == "captured"
