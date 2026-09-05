"""Checkout abandonment recovery.

The odd one out in this system: nothing was declined. There is no gateway
failure to classify, no attempt to retry, and no mandate to charge — the
customer simply left. The only lever is asking them to come back, which is why
it gets its own action rather than reusing a retry.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.classifier.engine import FailureClassifier
from app.executor.base import _EXECUTABLE_ACTIONS
from app.main import app
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.outreach.dispatcher import _CONTACT_ACTIONS
from app.policy.engine import RecoveryPolicyEngine
from app.policy.result import PolicyAction
from app.razorpay.executor import _CHARGE_ACTIONS, _LINK_ACTIONS

client = TestClient(app)


def _abandoned(code: str = "CHECKOUT_ABANDONED") -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id=f"evt_ab_{code}",
        razorpay_payment_id=f"pay_ab_{code}",
        merchant_id="m1",
        customer_id="c1",
        type=TransactionType.ONE_TIME,
        amount=249900,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code=code,
        error_description="Customer left the checkout",
        # Self-reported and never trusted.
        failure_category=FailureCategory.UNKNOWN,
        attempt_number=1,
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


class TestClassification:
    @pytest.mark.parametrize(
        "code", ["CHECKOUT_ABANDONED", "CART_ABANDONED", "PAYMENT_NOT_INITIATED"]
    )
    def test_checkout_telemetry_codes_classify(self, code: str) -> None:
        result = FailureClassifier().classify(_abandoned(code))
        assert result.category == FailureCategory.CHECKOUT_ABANDONED
        assert result.rule_id == "code.checkout_abandoned"

    def test_it_is_not_confused_with_a_decline(self) -> None:
        """A decline and an abandonment need opposite interventions."""
        declined = _abandoned()
        declined = declined.model_copy(update={"error_code": "BANK_DECLINED"})
        assert (
            FailureClassifier().classify(declined).category
            == FailureCategory.BANK_DECLINE
        )


class TestPolicy:
    def test_the_action_is_to_ask_the_customer_back(self) -> None:
        actions = RecoveryPolicyEngine.permitted_actions_for(
            FailureCategory.CHECKOUT_ABANDONED
        )
        assert actions[0] == PolicyAction.SEND_CHECKOUT_LINK

    def test_no_retry_action_is_ever_permitted(self) -> None:
        """Nothing was declined, so there is nothing to retry."""
        permitted = RecoveryPolicyEngine.permitted_actions_for(
            FailureCategory.CHECKOUT_ABANDONED
        )
        assert PolicyAction.SCHEDULED_RETRY not in permitted
        assert PolicyAction.IMMEDIATE_RETRY not in permitted

    def test_the_cadence_is_short_because_a_basket_goes_cold(self) -> None:
        window = RecoveryPolicyEngine.cooldown_window_for(
            FailureCategory.CHECKOUT_ABANDONED
        )
        assert window["default_seconds"] == 3600
        # An hour, not the 24h a funds retry waits for a salary to land.
        assert window["default_seconds"] < 86_400

    def test_nudging_stops_before_it_becomes_harassment(self) -> None:
        from app.policy.engine import _CATEGORY_POLICIES

        assert _CATEGORY_POLICIES[FailureCategory.CHECKOUT_ABANDONED].max_retries == 2


class TestActionRouting:
    def test_the_action_is_executable(self) -> None:
        assert PolicyAction.SEND_CHECKOUT_LINK in _EXECUTABLE_ACTIONS

    def test_it_is_delivered_as_a_payment_link(self) -> None:
        assert PolicyAction.SEND_CHECKOUT_LINK in _LINK_ACTIONS

    def test_it_never_charges_a_mandate(self) -> None:
        """The customer never authorised anything, so there is no mandate."""
        assert PolicyAction.SEND_CHECKOUT_LINK not in _CHARGE_ACTIONS

    def test_it_counts_as_contacting_the_customer(self) -> None:
        assert PolicyAction.SEND_CHECKOUT_LINK in _CONTACT_ACTIONS


class TestEndToEnd:
    def test_an_abandoned_checkout_is_recovered_not_escalated(self) -> None:
        body = client.post(
            "/api/dashboard/process", json=_abandoned().model_dump(mode="json")
        ).json()
        assert body["failure_category"] == "checkout_abandoned"
        assert body["policy_action"] == "send_checkout_link"
        assert body["automatic_recovery_allowed"] is True

    def test_it_appears_in_a_batch(self) -> None:
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 120, "seed": 11}
        ).json()
        scenarios = {row["scenario"] for row in body["by_scenario"]}
        assert "checkout_abandoned" in scenarios
