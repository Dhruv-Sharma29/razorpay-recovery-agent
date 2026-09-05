"""Tests for the AI recommendation boundary and policy isolation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from app.classifier.engine import FailureClassifier
from app.audit.store import AuditLogger
from app.escalation.handler import EscalationHandler
from app.executor.mock import MockExecutor
from app.models.payment_event import FailedTransactionEvent
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.policy.result import PolicyAction, RecommendationStatus
from app.recommendation.engine import RecoveryRecommender
from app.recommendation.result import ApprovedPaymentHistory
from app.reasoning.engine import RecoveryReasoner

URL = "http://nim.test/v1"


def _event(*, attempt: int = 1, amount: int = 149900) -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id="evt_recommendation_1",
        razorpay_payment_id="pay_recommendation_1",
        merchant_id="merchant_1",
        customer_id="customer_1",
        type="subscription",
        amount=amount,
        currency="INR",
        payment_method="upi",
        error_code="INSUFFICIENT_FUNDS",
        error_description="Payment failed because of insufficient funds",
        failure_category="unknown",
        attempt_number=attempt,
        mandate_status="active",
        timestamp=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc),
    )


def _response(body: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(body)}}]},
        request=httpx.Request("POST", URL),
    )


def _body(**overrides):
    body = {
        "revenue_at_risk": True,
        "risk_score": 0.91,
        "suggested_cause": "insufficient_funds",
        "suggested_action": "scheduled_retry",
        "confidence": 0.94,
        "evidence": ["Active subscription", "Insufficient-funds signal"],
    }
    body.update(overrides)
    return body


class TestRecoveryRecommender:
    def test_prompt_contains_only_aggregate_approved_history(self):
        recommender = RecoveryRecommender(
            nim_api_key="key", nim_base_url=URL, nim_model="test-model"
        )
        history = ApprovedPaymentHistory(
            prior_event_count=3,
            successful_payment_count=2,
            failed_payment_count=1,
            recovered_amount=20000,
            prior_recovery_attempts=1,
            last_outcome="recovered",
            last_failure_category="insufficient_funds",
            recent_outcomes=["recovered", "denied"],
        )
        with patch.object(httpx.Client, "post", return_value=_response(_body())) as post:
            result = recommender.recommend(
                _event(), FailureClassifier().classify(_event()), history
            )

        assert result.success is True
        payload = post.call_args.kwargs["json"]
        prompt = payload["messages"][1]["content"]
        assert "approved_payment_history" in prompt
        assert "prior_event_count" in prompt
        assert "customer_1" not in prompt
        assert "pay_recommendation_1" not in prompt

    def test_valid_model_output_is_structured(self):
        recommender = RecoveryRecommender(
            nim_api_key="key", nim_base_url=URL, nim_model="test-model"
        )
        with patch.object(httpx.Client, "post", return_value=_response(_body())):
            result = recommender.recommend(_event())

        assert result.success is True
        assert result.revenue_at_risk is True
        assert result.suggested_action == PolicyAction.SCHEDULED_RETRY
        assert result.suggested_cause.value == "insufficient_funds"
        assert result.is_fallback is False

    def test_missing_key_returns_safe_advisory_fallback(self):
        recommender = RecoveryRecommender(nim_api_key="", nim_base_url=URL)
        result = recommender.recommend(
            _event(), FailureClassifier().classify(_event())
        )

        assert result.success is False
        assert result.is_fallback is True
        assert result.suggested_action is None
        assert result.confidence == 0.0

    @pytest.mark.parametrize(
        "bad_body",
        [
            {"revenue_at_risk": "yes"},
            _body(risk_score=2.0),
            _body(suggested_action="charge_customer_now"),
            _body(evidence="not-a-list"),
        ],
    )
    def test_invalid_model_output_fails_closed(self, bad_body):
        recommender = RecoveryRecommender(
            nim_api_key="key", nim_base_url=URL, nim_model="test-model"
        )
        with patch.object(httpx.Client, "post", return_value=_response(bad_body)):
            result = recommender.recommend(_event())

        assert result.success is False
        assert result.is_fallback is True
        assert result.suggested_action is None


class TestPolicyRecommendationIsolation:
    def test_matching_recommendation_is_recorded_as_accepted(self):
        event = _event()
        classification = FailureClassifier().classify(event)
        recommendation = RecoveryRecommender(nim_api_key="").recommend(
            event, classification
        )
        # Simulate a live, validated recommendation while keeping the test
        # independent of the network.
        recommendation.success = True
        recommendation.is_fallback = False
        recommendation.suggested_action = PolicyAction.SCHEDULED_RETRY
        recommendation.confidence = 0.94

        decision = RecoveryPolicyEngine().evaluate(event, classification, recommendation)

        assert decision.action == PolicyAction.SCHEDULED_RETRY
        assert decision.automatic_recovery_allowed is True
        assert decision.recommendation_status == RecommendationStatus.ACCEPTED

    def test_model_cannot_change_deterministic_action(self):
        event = _event()
        classification = FailureClassifier().classify(event)
        recommendation = RecoveryRecommender(nim_api_key="").recommend(
            event, classification
        )
        recommendation.success = True
        recommendation.is_fallback = False
        recommendation.suggested_action = PolicyAction.IMMEDIATE_RETRY
        recommendation.confidence = 0.99

        decision = RecoveryPolicyEngine().evaluate(event, classification, recommendation)

        assert decision.action == PolicyAction.SCHEDULED_RETRY
        assert decision.recommendation_status == RecommendationStatus.CONSTRAINED
        assert decision.automatic_recovery_allowed is True

    def test_model_cannot_rescue_an_over_cap_payment(self):
        event = _event(amount=600000)
        classification = FailureClassifier().classify(event)
        recommendation = RecoveryRecommender(nim_api_key="").recommend(
            event, classification
        )
        recommendation.success = True
        recommendation.is_fallback = False
        recommendation.suggested_action = PolicyAction.SCHEDULED_RETRY

        decision = RecoveryPolicyEngine().evaluate(event, classification, recommendation)

        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False


class TestRecommendationPipelineBoundary:
    def test_pipeline_audits_ai_suggestion_and_final_policy_separately(self, tmp_path):
        pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            recommender=RecoveryRecommender(nim_api_key=""),
            reasoner=RecoveryReasoner(nim_api_key=""),
            executor=MockExecutor(),
            escalation_handler=EscalationHandler(),
            audit_logger=AuditLogger(f"sqlite:///{tmp_path}/audit.db"),
        )

        result = pipeline.process(_event())
        record = result.audit_write.record

        assert result.recommendation is not None
        assert result.recommendation.is_fallback is True
        assert result.policy_decision.action == PolicyAction.SCHEDULED_RETRY
        assert result.policy_decision.recommendation_status == RecommendationStatus.UNAVAILABLE
        assert record.recommendation_revenue_at_risk is True
        assert record.recommendation_suggested_cause == "insufficient_funds"
        assert record.recommendation_suggested_action is None
        assert record.recommendation_evidence
        assert record.recommendation_model == result.recommendation.model_id
        assert record.policy_action == "scheduled_retry"

    def test_audit_history_is_aggregate_and_bounded(self):
        audit = AuditLogger("sqlite:///:memory:")
        event = _event()
        pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            recommender=RecoveryRecommender(nim_api_key=""),
            reasoner=RecoveryReasoner(nim_api_key=""),
            executor=MockExecutor(),
            escalation_handler=EscalationHandler(),
            audit_logger=audit,
        )
        pipeline.process(event)

        history = audit.get_approved_payment_history(event.customer_id)
        assert history.prior_event_count == 1
        assert history.last_outcome == "recovered"
        assert history.last_failure_category == "insufficient_funds"
        assert not hasattr(history, "customer_id")
