"""AI contribution field tests.

The model's value-add is plain-language framing an operator can act on.
It still cannot authorize anything, and its customer-facing copy must not
promise an outcome the policy has not granted.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from app.classifier.result import ClassificationCertainty, ClassificationResult, FailureCategory
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyAction, PolicyDecision
from app.reasoning.engine import RecoveryReasoner

URL = "http://nim.test/v1"


def _event():
    return FailedTransactionEvent(
        event_id="evt_ai_1",
        razorpay_payment_id="pay_ai_1",
        merchant_id="m1",
        customer_id="c1",
        type="one_time",
        amount=149900,
        currency="INR",
        payment_method="upi",
        error_code="INSUFFICIENT_FUNDS",
        error_description="Insufficient funds",
        failure_category="insufficient_funds",
        attempt_number=1,
        mandate_status=None,
        timestamp=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
    )


def _classification():
    return ClassificationResult(
        category=FailureCategory.INSUFFICIENT_FUNDS,
        confidence=1.0,
        certainty=ClassificationCertainty.HIGH,
        reason="Error code INSUFFICIENT_FUNDS maps to insufficient_funds",
        rule_id="code.insufficient_funds",
        source_field="error_code",
    )


def _policy(allowed=True, escalate=False):
    return PolicyDecision(
        action=PolicyAction.SCHEDULED_RETRY if allowed else PolicyAction.ESCALATE,
        automatic_recovery_allowed=allowed,
        reason="Within retry limits" if allowed else "Retry limit exhausted",
        rule_id="policy.insufficient_funds.retry_24h",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        escalation_required=escalate,
        escalation_reason=None,
        max_retries_for_category=2,
        current_attempt=1,
        amount=149900,
        amount_limit=500000,
    )


def _response(body: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(body)}}]},
        request=httpx.Request("POST", URL),
    )


def _analyze(model_body: dict, policy=None):
    reasoner = RecoveryReasoner(nim_api_key="k", nim_base_url=URL, nim_model="m")
    with patch.object(httpx.Client, "post", return_value=_response(model_body)):
        return reasoner.analyze(_event(), _classification(), policy or _policy())


BASE = {
    "recommendation": "Retry after the cooldown",
    "explanation": "The balance was short at the time of the attempt.",
    "confidence": 0.8,
}


class TestFieldsParse:
    def test_all_four_fields_are_captured(self):
        result = _analyze({
            **BASE,
            "root_cause_plain": "The customer's account did not have enough money.",
            "why_appropriate": "Waiting a day gives the balance time to recover.",
            "customer_message": "We could not complete your payment. Please check your account and try again.",
            "escalation_summary": "No review needed.",
        })
        assert result.root_cause_plain.startswith("The customer's account")
        assert result.why_appropriate.startswith("Waiting a day")
        assert result.customer_message.startswith("We could not complete")
        assert result.escalation_summary == "No review needed."
        assert result.is_fallback is False

    def test_missing_optional_fields_degrade_to_none(self):
        """A model that omits them must still produce a usable explanation."""
        result = _analyze(BASE)
        assert result.success is True
        assert result.root_cause_plain is None
        assert result.customer_message is None

    def test_blank_and_wrong_typed_fields_are_dropped(self):
        result = _analyze({
            **BASE,
            "root_cause_plain": "   ",
            "why_appropriate": 42,
            "escalation_summary": None,
        })
        assert result.root_cause_plain is None
        assert result.why_appropriate is None
        assert result.escalation_summary is None


class TestCustomerMessageSafety:
    @pytest.mark.parametrize(
        "unsafe",
        [
            "We have authorized a refund for your payment.",
            "Your refund is on the way.",
            "This retry is guaranteed to succeed.",
        ],
    )
    def test_over_promising_copy_is_rejected(self, unsafe):
        result = _analyze({**BASE, "customer_message": unsafe})
        assert result.customer_message is None

    def test_copy_with_amounts_is_rejected(self):
        result = _analyze(
            {**BASE, "customer_message": "Your payment of 1499 could not be completed."}
        )
        assert result.customer_message is None

    def test_safe_copy_survives(self):
        safe = "We were unable to process your payment. Please check your account details."
        result = _analyze({**BASE, "customer_message": safe})
        assert result.customer_message == safe


class TestFallbackStillLabelled:
    def test_fallback_fills_fields_from_the_rule_that_fired(self):
        """With no API key the engine short-circuits to the fallback."""
        reasoner = RecoveryReasoner(nim_api_key="", nim_base_url=URL, nim_model="m")
        result = reasoner.analyze(_event(), _classification(), _policy())

        assert result.is_fallback is True
        # Grounded in the real rule, so the operator view is never blank.
        assert result.root_cause_plain == _classification().reason
        assert result.why_appropriate == _policy().reason
        # No model, so no generated customer copy.
        assert result.customer_message is None

    def test_fallback_never_authorizes_a_denied_action(self):
        reasoner = RecoveryReasoner(nim_api_key="", nim_base_url=URL, nim_model="m")
        denied = _policy(allowed=False, escalate=True)
        result = reasoner.analyze(_event(), _classification(), denied)

        assert result.policy_action_allowed is False
        assert result.escalation_summary == denied.reason

    def test_model_cannot_upgrade_a_denial(self):
        """Even a model claiming approval cannot widen the policy."""
        denied = _policy(allowed=False, escalate=True)
        result = _analyze(
            {**BASE, "recommendation": "Approve and retry immediately"}, policy=denied
        )
        assert result.policy_action_allowed is False
