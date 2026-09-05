"""Bounded timing choice.

For insufficient funds the cooldown *is* the intervention — an account empty
today is often funded on a salary date — so letting the advisor move the wait
is worth more than letting it pick the action. The bound has the same shape as
the action bound: policy publishes a window, the advisor may move inside it,
and anything outside is discarded rather than clamped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.escalation.handler import EscalationHandler
from app.executor.mock import MockExecutor
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.persistence.store import RecoveryStateStore
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.reasoning.engine import RecoveryReasoner
from app.recommendation.result import RecoveryRecommendation

DEFAULT = 86_400
MIN_OK = 43_200
MAX_OK = 259_200


def _pipeline(delay: int | None, confidence: float = 0.95):
    recommender = MagicMock()
    recommender.recommend.return_value = RecoveryRecommendation(
        success=True,
        revenue_at_risk=True,
        risk_score=0.8,
        suggested_cause=FailureCategory.INSUFFICIENT_FUNDS,
        suggested_action=None,
        confidence=confidence,
        suggested_delay_seconds=delay,
        model_id="test-model",
    )
    return RecoveryPipeline(
        classifier=FailureClassifier(),
        policy_engine=RecoveryPolicyEngine(),
        reasoner=RecoveryReasoner(),
        executor=MockExecutor(),
        escalation_handler=EscalationHandler(),
        audit_logger=AuditLogger(database_url="sqlite:///:memory:"),
        state_store=RecoveryStateStore(database_url="sqlite:///:memory:"),
        recommender=recommender,
    )


def _event() -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id="evt_time_1",
        razorpay_payment_id="pay_time_1",
        merchant_id="m1",
        customer_id="c1",
        type=TransactionType.ONE_TIME,
        amount=100000,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code="INSUFFICIENT_FUNDS",
        error_description="Not enough balance",
        failure_category=FailureCategory.UNKNOWN,
        attempt_number=1,
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


class TestPolicyPublishesAWindow:
    def test_a_cause_with_a_cooldown_publishes_its_bounds(self) -> None:
        window = RecoveryPolicyEngine.cooldown_window_for(
            FailureCategory.INSUFFICIENT_FUNDS
        )
        assert window["default_seconds"] == DEFAULT
        assert window["min_seconds"] == MIN_OK
        assert window["max_seconds"] == MAX_OK

    def test_a_cause_with_no_cooldown_publishes_nothing(self) -> None:
        """Silence is how the advisor is told there is no decision here."""
        assert RecoveryPolicyEngine.cooldown_window_for(
            FailureCategory.NETWORK_ERROR
        ) == {}


class TestTheAdvisorMayMoveInsideTheWindow:
    @pytest.mark.parametrize("delay", [MIN_OK, 100_000, MAX_OK])
    def test_a_wait_inside_the_window_is_honoured(self, delay: int) -> None:
        result = _pipeline(delay).process(_event())
        assert result.execution.recovery_delay_seconds == delay
        assert result.delay_source == "model"

    def test_no_suggestion_leaves_the_policy_default(self) -> None:
        result = _pipeline(None).process(_event())
        assert result.execution.recovery_delay_seconds == DEFAULT
        assert result.delay_source == "policy"


class TestTheWindowIsAHardBound:
    @pytest.mark.parametrize("delay", [0, MIN_OK - 1, MAX_OK + 1, 99_999_999])
    def test_a_wait_outside_the_window_is_discarded(self, delay: int) -> None:
        """Discarded, not clamped: snapping 3 years to 72h would record a
        choice the model never made."""
        result = _pipeline(delay).process(_event())
        assert result.execution.recovery_delay_seconds == DEFAULT
        assert result.delay_source == "policy"

    def test_an_unsure_advisor_cannot_move_the_wait(self) -> None:
        result = _pipeline(MAX_OK, confidence=0.2).process(_event())
        assert result.execution.recovery_delay_seconds == DEFAULT
        assert result.delay_source == "policy"

    def test_timing_choice_never_removes_the_cooldown(self) -> None:
        """Whatever the advisor asks for, a deferred retry stays deferred."""
        for delay in (0, 1, MIN_OK, MAX_OK, 10**9):
            result = _pipeline(delay).process(_event())
            assert result.execution.recovery_delay_seconds > 0
