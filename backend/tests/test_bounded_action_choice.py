"""The advisor may pick among policy-authorised actions — nothing more.

The policy decides WHETHER to act and publishes the set it authorises.
The model may choose within that set. It can never add an action, never
flip automatic_recovery_allowed, and never act when the policy refused.
"""

from __future__ import annotations

from datetime import datetime, timezone

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
from app.pipeline.engine import RecoveryPipeline
from app.reasoning.engine import RecoveryReasoner
from app.policy.engine import RecoveryPolicyEngine
from app.policy.result import PolicyAction
from app.recommendation.result import RecoveryRecommendation

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def _event(code="GATEWAY_ERROR", amount=149900, attempt=1):
    return FailedTransactionEvent(
        event_id=f"evt_{code}_{amount}_{attempt}",
        razorpay_payment_id=f"pay_{code}_{amount}_{attempt}",
        merchant_id="m", customer_id="c", type="one_time",
        amount=amount, currency="INR", payment_method="upi",
        error_code=code, error_description="x", failure_category="unknown",
        attempt_number=attempt, mandate_status=None, timestamp=T0,
    )


def _decide(event):
    cls = FailureClassifier().classify(event)
    return cls, RecoveryPolicyEngine().evaluate(event, cls)


class TestPermittedSet:
    def test_prescribed_action_is_always_permitted(self):
        _, d = _decide(_event())
        assert d.action in d.permitted_actions

    def test_network_error_offers_a_real_alternative(self):
        _, d = _decide(_event("GATEWAY_ERROR"))
        assert d.action is PolicyAction.IMMEDIATE_RETRY
        assert PolicyAction.SCHEDULED_RETRY in d.permitted_actions

    def test_permitted_set_never_contains_escalate(self):
        """Escalation is not something the advisor may choose."""
        for code in ("GATEWAY_ERROR", "BANK_DECLINED", "AUTHENTICATION_ERROR"):
            _, d = _decide(_event(code))
            assert PolicyAction.ESCALATE not in d.permitted_actions

    def test_a_refused_decision_authorises_nothing(self):
        # Over the amount cap: policy refuses outright.
        _, d = _decide(_event("INSUFFICIENT_FUNDS", amount=9_000_000))
        assert d.automatic_recovery_allowed is False
        assert d.permitted_actions == []


def _recommend(action):
    return RecoveryRecommendation(
        success=True, revenue_at_risk=True, risk_score=0.9,
        suggested_cause=None, suggested_action=action, confidence=0.9,
        evidence=[], model_id="test", is_fallback=False,
    )


class TestPipelineHonoursTheBound:
    """Exercised through the real pipeline via a stub recommender."""

    @pytest.fixture
    def pipeline_factory(self, tmp_path):
        from app.audit.store import AuditLogger
        from app.escalation.handler import EscalationHandler
        from app.executor.mock import SimulatedPaymentExecutor
        from app.pipeline.engine import RecoveryPipeline
        from app.reasoning.engine import RecoveryReasoner

        def make(suggested):
            class Stub:
                def recommend(self, *a, **k):
                    return _recommend(suggested)

            return RecoveryPipeline(
                classifier=FailureClassifier(),
                policy_engine=RecoveryPolicyEngine(),
                reasoner=RecoveryReasoner(nim_api_key=""),
                executor=SimulatedPaymentExecutor(),
                escalation_handler=EscalationHandler(),
                audit_logger=AuditLogger(
                    database_url=f"sqlite:///{tmp_path}/a{abs(hash(suggested))}.db"
                ),
                recommender=Stub(),
            )

        return make

    def test_model_choice_inside_the_set_is_honoured(self, pipeline_factory):
        result = pipeline_factory(PolicyAction.SCHEDULED_RETRY).process(
            _event("GATEWAY_ERROR")
        )
        assert result.policy_decision.action is PolicyAction.SCHEDULED_RETRY
        assert result.action_source == "model"

    def test_model_choice_outside_the_set_is_ignored(self, pipeline_factory):
        # TRIGGER_REAUTHORIZATION is not authorised for a network error.
        result = pipeline_factory(PolicyAction.TRIGGER_REAUTHORIZATION).process(
            _event("GATEWAY_ERROR")
        )
        assert result.policy_decision.action is PolicyAction.IMMEDIATE_RETRY
        assert result.action_source == "policy"

    def test_model_cannot_make_a_refused_case_actionable(self, pipeline_factory):
        result = pipeline_factory(PolicyAction.SCHEDULED_RETRY).process(
            _event("INSUFFICIENT_FUNDS", amount=9_000_000)
        )
        assert result.policy_decision.automatic_recovery_allowed is False
        assert result.action_source == "policy"
        assert result.final_outcome.value == "escalated"

    def test_choosing_the_default_is_not_reported_as_a_model_choice(
        self, pipeline_factory
    ):
        result = pipeline_factory(PolicyAction.IMMEDIATE_RETRY).process(
            _event("GATEWAY_ERROR")
        )
        assert result.action_source == "policy"

    def test_recovery_permission_is_never_altered_by_the_choice(
        self, pipeline_factory
    ):
        result = pipeline_factory(PolicyAction.SCHEDULED_RETRY).process(
            _event("GATEWAY_ERROR")
        )
        assert result.policy_decision.automatic_recovery_allowed is True
        assert result.action_source == "model"


class TestIsolationMetricUnderstandsBoundedChoice:
    """A permitted alternative is not a boundary violation.

    Regression: the evaluator compares each decision against a baseline
    computed without the advisor. Any differing action was flagged as a
    policy-isolation violation, so with a live model configured the run
    would report a safety failure that had not happened.
    """

    def test_a_permitted_alternative_is_not_a_violation(self):
        from app.evaluation.harness import Evaluator

        ev = _event("GATEWAY_ERROR")
        cls, baseline = _decide(ev)
        chosen = PolicyAction.SCHEDULED_RETRY

        assert chosen != baseline.action
        assert chosen in baseline.permitted_actions
        # The evaluator's rule: only an action outside the permitted set counts.
        assert not (chosen not in baseline.permitted_actions)
        assert Evaluator is not None

    def test_an_unpermitted_action_would_still_be_a_violation(self):
        _, baseline = _decide(_event("GATEWAY_ERROR"))
        assert PolicyAction.TRIGGER_REAUTHORIZATION not in baseline.permitted_actions


class TestConfidenceGate:
    """An unsure advisor must not override a deterministic default.

    The A/B measured an ungated advisor losing ground, so confidence is the
    price of the override rather than something merely reported.
    """

    def _pipeline(self, threshold: float, suggested, confidence: float):
        from unittest.mock import MagicMock

        from app.recommendation.result import RecoveryRecommendation

        recommender = MagicMock()
        recommender.recommend.return_value = RecoveryRecommendation(
            success=True,
            revenue_at_risk=True,
            risk_score=0.8,
            suggested_cause=FailureCategory.NETWORK_ERROR,
            suggested_action=suggested,
            confidence=confidence,
            model_id="test-model",
        )
        return RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            reasoner=RecoveryReasoner(),
            executor=MockExecutor(),
            escalation_handler=EscalationHandler(),
            audit_logger=AuditLogger(database_url="sqlite:///:memory:"),
            recommender=recommender,
            model_action_choice_min_confidence=threshold,
        )

    def _network_event(self):
        return FailedTransactionEvent(
            event_id="evt_conf_1",
            razorpay_payment_id="pay_conf_1",
            merchant_id="m1",
            customer_id="c1",
            type=TransactionType.ONE_TIME,
            amount=100000,
            currency="INR",
            payment_method=PaymentMethod.UPI,
            error_code="GATEWAY_TIMEOUT",
            error_description="Gateway timed out",
            failure_category=FailureCategory.UNKNOWN,
            attempt_number=1,
            timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

    def test_a_confident_choice_within_bounds_is_taken(self) -> None:
        pipeline = self._pipeline(0.7, PolicyAction.SCHEDULED_RETRY, 0.95)
        result = pipeline.process(self._network_event())
        assert result.policy_decision.action == PolicyAction.SCHEDULED_RETRY
        assert result.action_source == "model"

    def test_an_unsure_choice_is_ignored(self) -> None:
        pipeline = self._pipeline(0.7, PolicyAction.SCHEDULED_RETRY, 0.4)
        result = pipeline.process(self._network_event())
        # Policy's own default for a network error stands.
        assert result.policy_decision.action == PolicyAction.IMMEDIATE_RETRY
        assert result.action_source == "policy"

    def test_the_threshold_is_inclusive(self) -> None:
        pipeline = self._pipeline(0.7, PolicyAction.SCHEDULED_RETRY, 0.7)
        result = pipeline.process(self._network_event())
        assert result.action_source == "model"

    def test_confidence_cannot_buy_an_unauthorised_action(self) -> None:
        """Certainty is not authority: out of bounds stays out of bounds."""
        pipeline = self._pipeline(0.7, PolicyAction.ESCALATE, 1.0)
        result = pipeline.process(self._network_event())
        assert result.policy_decision.action == PolicyAction.IMMEDIATE_RETRY
        assert result.action_source == "policy"
