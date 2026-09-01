"""Tests for the append-only audit log (TASK-007 Part B).

No network access, no Razorpay credentials, no Ollama required.

Coverage:
  10. successful recovery can be audited
  11. denied recovery can be audited
  12. escalation can be audited
  13. execution failure can be audited
  14. audit record contains required fields
  15. multiple audit events remain separate
  16. previous audit events cannot be silently overwritten
  17. audit failure cannot authorize recovery
  18. secrets are not stored/logged
  19. payment event is not mutated
"""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.audit.result import AuditOutcome, AuditRecord, AuditWriteResult
from app.audit.store import AuditLogger, redact_secrets
from app.classifier.result import ClassificationCertainty, ClassificationResult
from app.escalation.handler import EscalationHandler
from app.escalation.result import EscalationStatus
from app.executor.result import ExecutionResult, ExecutionStatus
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.policy.result import EscalationReason, PolicyAction, PolicyDecision
from app.reasoning.result import ReasoningResult


def _make_event(
    event_id: str = "evt_aud_001",
    payment_id: str = "pay_aud_001",
    amount: int = 149900,
    attempt: int = 1,
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
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        attempt_number=attempt,
        mandate_status=None,
        timestamp=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
    )


def _make_classification() -> ClassificationResult:
    return ClassificationResult(
        category=FailureCategory.INSUFFICIENT_FUNDS,
        confidence=1.0,
        certainty=ClassificationCertainty.HIGH,
        reason="Error code INSUFFICIENT_FUNDS maps to insufficient_funds",
        rule_id="code.insufficient_funds",
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


def _make_denied_decision() -> PolicyDecision:
    return PolicyDecision(
        action=PolicyAction.ESCALATE,
        automatic_recovery_allowed=False,
        reason="Retry limit exhausted for insufficient_funds",
        rule_id="policy.insufficient_funds.retry_limit_exhausted",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        escalation_required=True,
        escalation_reason=EscalationReason.RETRY_LIMIT_EXHAUSTED,
        max_retries_for_category=2,
        current_attempt=3,
        amount=149900,
        amount_limit=500000,
    )


def _make_reasoning(*, success: bool = True, error: str | None = None) -> ReasoningResult:
    return ReasoningResult(
        success=success,
        recommendation="Follow policy",
        explanation="Policy decision stands",
        confidence=0.9 if success else 0.0,
        model_id="qwen3.5:test" if success else "fallback",
        policy_action_allowed=True if success else False,
        is_fallback=not success,
        error=error,
    )


def _make_execution(
    *,
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    executed: bool = True,
    error: str | None = None,
    execution_id: str = "exec_aud_1",
) -> ExecutionResult:
    return ExecutionResult(
        status=status,
        action_attempted="scheduled_retry",
        payment_id="pay_aud_001",
        event_id="evt_aud_001",
        executed=executed,
        execution_id=execution_id,
        idempotency_key="idem_aud_1",
        error=error,
        reason="ok" if executed else "failed",
        timestamp=datetime(2026, 9, 1, 10, 0, 5, tzinfo=timezone.utc),
    )


@pytest.fixture()
def audit_logger() -> AuditLogger:
    logger = AuditLogger(database_url="sqlite:///:memory:")
    yield logger
    logger.close()


class TestSuccessfulRecoveryAudited:
    def test_successful_recovery_is_recorded(self, audit_logger):
        event = _make_event()
        policy = _make_allowed_decision()
        write = audit_logger.record(
            event,
            classification=_make_classification(),
            policy_decision=policy,
            reasoning=_make_reasoning(),
            execution=_make_execution(),
        )
        assert write.recorded is True
        assert write.authorizes_recovery is False
        assert write.record is not None
        assert write.record.final_outcome == AuditOutcome.RECOVERED
        assert write.record.execution_status == ExecutionStatus.SUCCESS.value
        assert write.record.policy_action == PolicyAction.SCHEDULED_RETRY.value


class TestDeniedRecoveryAudited:
    def test_denied_recovery_is_recorded(self, audit_logger):
        event = _make_event()
        policy = _make_denied_decision()
        write = audit_logger.record(
            event,
            classification=_make_classification(),
            policy_decision=policy,
            reasoning=_make_reasoning(),
        )
        assert write.recorded is True
        assert write.authorizes_recovery is False
        assert write.record is not None
        assert write.record.final_outcome == AuditOutcome.DENIED
        assert write.record.automatic_recovery_allowed is False
        assert policy.automatic_recovery_allowed is False
        assert policy.action == PolicyAction.ESCALATE


class TestEscalationAudited:
    def test_escalation_is_recorded(self, audit_logger):
        event = _make_event()
        policy = _make_denied_decision()
        escalation = EscalationHandler().handle(
            event, policy, classification=_make_classification()
        )
        write = audit_logger.record(
            event,
            classification=_make_classification(),
            policy_decision=policy,
            escalation=escalation,
        )
        assert write.recorded is True
        assert write.record is not None
        assert write.record.final_outcome == AuditOutcome.ESCALATED
        assert write.record.escalation_status == EscalationStatus.OPEN.value
        assert write.record.escalation_reference == escalation.escalation_id
        assert write.authorizes_recovery is False


class TestExecutionFailureAudited:
    def test_execution_failure_is_recorded(self, audit_logger):
        event = _make_event()
        policy = _make_allowed_decision()
        execution = _make_execution(
            status=ExecutionStatus.FAILED,
            executed=False,
            error="Razorpay timeout",
        )
        write = audit_logger.record(
            event,
            classification=_make_classification(),
            policy_decision=policy,
            execution=execution,
        )
        assert write.recorded is True
        assert write.record is not None
        assert write.record.final_outcome == AuditOutcome.EXECUTION_FAILED
        assert write.record.error == "Razorpay timeout"
        assert write.record.execution_status == ExecutionStatus.FAILED.value
        assert write.authorizes_recovery is False


class TestAuditRecordRequiredFields:
    def test_required_fields_present(self, audit_logger):
        event = _make_event()
        policy = _make_allowed_decision()
        classification = _make_classification()
        reasoning = _make_reasoning()
        execution = _make_execution()
        escalation = EscalationHandler().handle(
            event,
            policy,
            classification=classification,
            reasoning=reasoning,
            execution=execution,
        )
        ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        write = audit_logger.record(
            event,
            classification=classification,
            policy_decision=policy,
            reasoning=reasoning,
            execution=execution,
            escalation=escalation,
            timestamp=ts,
        )
        record = write.record
        assert record is not None
        assert record.audit_id
        assert record.event_id == event.event_id
        assert record.payment_id == event.razorpay_payment_id
        assert record.timestamp == ts
        assert record.classification_category == classification.category.value
        assert record.classification_reason == classification.reason
        assert record.policy_action == policy.action.value
        assert record.policy_reason == policy.reason
        assert record.reasoning_reference == reasoning.model_id
        assert record.execution_reference == execution.execution_id
        assert record.escalation_reference == escalation.escalation_id
        assert record.final_outcome is not None
        assert isinstance(record, AuditRecord)


class TestMultipleAuditEventsRemainSeparate:
    def test_two_records_are_distinct_rows(self, audit_logger):
        first = audit_logger.record(
            _make_event(event_id="evt_a", payment_id="pay_a"),
            policy_decision=_make_allowed_decision(),
            execution=_make_execution(),
        )
        second = audit_logger.record(
            _make_event(event_id="evt_b", payment_id="pay_b"),
            policy_decision=_make_denied_decision(),
        )
        assert first.audit_id != second.audit_id
        rows = audit_logger.list_records()
        assert len(rows) == 2
        assert rows[0].event_id == "evt_a"
        assert rows[1].event_id == "evt_b"
        assert rows[0].final_outcome == AuditOutcome.RECOVERED
        assert rows[1].final_outcome == AuditOutcome.DENIED


class TestNoSilentOverwrite:
    def test_same_event_appends_second_row(self, audit_logger):
        event = _make_event()
        first = audit_logger.record(event, policy_decision=_make_allowed_decision())
        second = audit_logger.record(event, policy_decision=_make_denied_decision())
        rows = audit_logger.list_records()
        assert len(rows) == 2
        assert first.audit_id != second.audit_id
        assert rows[0].audit_id == first.audit_id
        assert rows[1].audit_id == second.audit_id
        assert rows[0].policy_action == PolicyAction.SCHEDULED_RETRY.value
        assert rows[1].policy_action == PolicyAction.ESCALATE.value

    def test_store_has_no_update_or_delete_api(self, audit_logger):
        assert not hasattr(AuditLogger, "update")
        assert not hasattr(AuditLogger, "delete")
        assert not hasattr(audit_logger, "overwrite")

    def test_sqlite_has_no_update_in_module_source(self):
        source = Path("app/audit/store.py").read_text(encoding="utf-8").lower()
        assert "update audit_log" not in source
        assert "delete from audit_log" not in source
        assert "insert into audit_log" in source


class TestAuditFailureDoesNotAuthorize:
    def test_write_failure_never_authorizes(self, audit_logger):
        event = _make_event()
        policy = _make_allowed_decision()
        audit_logger.close()
        write = audit_logger.record(event, policy_decision=policy)
        assert write.recorded is False
        assert write.authorizes_recovery is False
        assert write.record is None
        assert write.error
        assert policy.automatic_recovery_allowed is True
        assert isinstance(write, AuditWriteResult)

    def test_unique_constraint_does_not_replace_row(self, tmp_path):
        db_path = tmp_path / "audit.db"
        logger = AuditLogger(database_url=f"sqlite:///{db_path}")
        event = _make_event()
        first = logger.record(event, policy_decision=_make_allowed_decision())
        assert first.recorded is True
        conn = sqlite3.connect(str(db_path))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audit_log (audit_id, recorded_at, event_id, payment_id, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (first.audit_id, "now", "evt_x", "pay_x", "{}"),
            )
        conn.close()
        rows = logger.list_records()
        assert len(rows) == 1
        assert rows[0].event_id == event.event_id
        logger.close()


class TestSecretsNotStored:
    def test_api_keys_are_redacted_from_errors(self, audit_logger):
        event = _make_event()
        execution = _make_execution(
            status=ExecutionStatus.FAILED,
            executed=False,
            error="auth failed key_secret=sk_live_abc razorpay_key_id=rzp_test_abc123xyz",
        )
        write = audit_logger.record(
            event,
            policy_decision=_make_allowed_decision(),
            execution=execution,
            extra={
                "razorpay_key_secret": "super-secret",
                "error": "token=rzp_live_shouldnotpersist",
            },
        )
        assert write.recorded is True
        payload = write.record.model_dump(mode="json") if write.record else {}
        blob = json.dumps(payload)
        assert "sk_live_abc" not in blob
        assert "super-secret" not in blob
        assert "rzp_test_abc123xyz" not in blob
        assert "rzp_live_shouldnotpersist" not in blob
        stored = audit_logger.list_records()[0]
        stored_blob = json.dumps(stored.model_dump(mode="json"))
        assert "sk_live" not in stored_blob
        assert "super-secret" not in stored_blob

    def test_redact_secrets_helper(self):
        data = {
            "razorpay_key_secret": "abc",
            "nested": {"api_key": "xyz", "payment_id": "pay_ok"},
            "note": "secret=hunter2",
        }
        redacted = redact_secrets(data)
        assert redacted["razorpay_key_secret"] == "[REDACTED]"
        assert redacted["nested"]["api_key"] == "[REDACTED]"
        assert redacted["nested"]["payment_id"] == "pay_ok"
        assert "[REDACTED]" in redacted["note"]

    def test_customer_id_not_stored(self, audit_logger):
        event = _make_event()
        write = audit_logger.record(event, policy_decision=_make_allowed_decision())
        blob = json.dumps(write.record.model_dump(mode="json"))
        assert event.customer_id not in blob
        assert "cust_01" not in blob


class TestPaymentEventNotMutated:
    def test_record_does_not_mutate_event_or_policy(self, audit_logger):
        event = _make_event()
        policy = _make_allowed_decision()
        event_before = copy.deepcopy(event)
        policy_before = copy.deepcopy(policy)
        audit_logger.record(
            event,
            classification=_make_classification(),
            policy_decision=policy,
            reasoning=_make_reasoning(),
            execution=_make_execution(),
        )
        assert event.model_dump() == event_before.model_dump()
        assert policy.model_dump() == policy_before.model_dump()
