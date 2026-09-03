"""Tests for the quick-win hardening changes.

Covers: policy amount-limit config wiring, audit pagination/filter,
catastrophic executor-failure handling, and audit-endpoint pagination.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.classifier.result import ClassificationResult, ClassificationCertainty
from app.config import settings
from app.escalation.handler import EscalationHandler
from app.executor.result import ExecutionStatus
from app.main import app
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.reasoning.engine import RecoveryReasoner
from app.audit.result import AuditOutcome


def _event(event_id="evt_qw", payment_id="pay_qw", amount=149900, attempt=1) -> FailedTransactionEvent:
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
        error_description="insufficient balance",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        attempt_number=attempt,
        mandate_status=None,
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


# --- Item 1: policy engine reads the configured amount limit ---

class TestAmountLimitConfig:
    def test_default_limit_comes_from_settings(self):
        assert RecoveryPolicyEngine().amount_limit == settings.auto_recovery_amount_limit

    def test_explicit_limit_overrides(self):
        assert RecoveryPolicyEngine(amount_limit=12345).amount_limit == 12345

    def test_engine_picks_up_configured_value(self, monkeypatch):
        monkeypatch.setattr(settings, "auto_recovery_amount_limit", 999_00)
        assert RecoveryPolicyEngine().amount_limit == 999_00


# --- Item: audit pagination / count / filter ---

class TestAuditPagination:
    def _logger_with(self, n: int) -> AuditLogger:
        logger = AuditLogger("sqlite:///:memory:")
        for i in range(n):
            logger.record(_event(event_id=f"evt_{i:03d}", payment_id=f"pay_{i:03d}"))
        return logger

    def test_count_records(self):
        logger = self._logger_with(5)
        assert logger.count_records() == 5

    def test_limit_and_offset(self):
        logger = self._logger_with(5)
        assert len(logger.list_records(limit=2)) == 2
        page2 = logger.list_records(limit=2, offset=2)
        assert len(page2) == 2
        # oldest-first, so offset 2 skips the first two
        assert page2[0].event_id == "evt_002"

    def test_list_all_by_default(self):
        logger = self._logger_with(3)
        assert len(logger.list_records()) == 3

    def test_outcome_filter(self):
        logger = self._logger_with(3)
        # These records have no execution/escalation -> RECORDED
        assert logger.count_records(outcome="recorded") == 3
        assert logger.count_records(outcome="recovered") == 0


# --- Item 4: catastrophic executor failure never leaves execution None ---

class _RaisingExecutor:
    def execute(self, payment_event, policy_decision):  # noqa: ANN001
        raise RuntimeError("boom: downstream crashed")


class TestCatastrophicExecutor:
    def test_executor_exception_becomes_failed_and_escalates(self):
        pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            reasoner=RecoveryReasoner(nim_api_key=""),  # no-key -> instant fallback
            executor=_RaisingExecutor(),  # raises on execute
            escalation_handler=EscalationHandler(),
            audit_logger=AuditLogger("sqlite:///:memory:"),
        )
        # insufficient_funds attempt 1 -> policy allows -> executor invoked -> raises
        result = pipeline.process(_event())

        assert result.execution is not None, "execution must not be None"
        assert result.execution.status == ExecutionStatus.FAILED
        assert result.final_outcome in (AuditOutcome.ESCALATED, AuditOutcome.EXECUTION_FAILED)
        assert result.final_outcome != AuditOutcome.RECOVERED
        assert result.escalation is not None


# --- Item: audit endpoint pagination through the API ---

@pytest.fixture(autouse=True)
def _mock_reasoner():
    from unittest.mock import patch
    from app.reasoning.result import ReasoningResult

    def fake(self, e, c, p):  # noqa: ANN001
        return ReasoningResult(
            success=True, recommendation="r", explanation="x", confidence=0.9,
            model_id="mock", policy_action_allowed=(p.automatic_recovery_allowed if p else False),
            is_fallback=False, error=None,
        )

    with patch.object(RecoveryReasoner, "analyze", fake):
        yield


class TestAuditEndpointPagination:
    def test_limit_caps_page_and_reports_total(self):
        client = TestClient(app)
        for i in range(3):
            client.post("/api/dashboard/process", json={
                "event_id": f"evt_api_{i}", "razorpay_payment_id": f"pay_api_{i}",
                "merchant_id": "m", "customer_id": "c", "type": "one_time",
                "amount": 149900, "currency": "INR", "payment_method": "upi",
                "error_code": "INSUFFICIENT_FUNDS", "error_description": "insufficient balance",
                "failure_category": "insufficient_funds", "attempt_number": 1,
                "mandate_status": None, "timestamp": "2026-09-01T10:00:00Z",
            })
        resp = client.get("/api/dashboard/audit", params={"limit": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert len(data["records"]) == 1
        assert data["total"] >= 3
