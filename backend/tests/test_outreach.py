"""Outreach tests.

"Contacted" must mean the customer was actually contacted — not that some
action ran. A silent retry against the payment rail is not contact.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.payment_event import FailedTransactionEvent
from app.outreach import OutreachChannel, SimulatedOutreachDispatcher
from app.policy.result import PolicyAction

MSG = "We could not complete your payment. Please update your details."


@pytest.fixture
def event():
    return FailedTransactionEvent(
        event_id="evt_out", razorpay_payment_id="pay_out", merchant_id="m",
        customer_id="c", type="one_time", amount=149900, currency="INR",
        payment_method="upi", error_code="EXPIRED_CARD", error_description="x",
        failure_category="unknown", attempt_number=1, mandate_status=None,
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


class TestWhenContactIsWarranted:
    @pytest.mark.parametrize("action", [
        PolicyAction.TRIGGER_REAUTHORIZATION,
        PolicyAction.RESEND_AUTH_PROMPT,
        PolicyAction.SWITCH_PAYMENT_METHOD,
    ])
    def test_customer_actions_are_contacted(self, event, action):
        r = SimulatedOutreachDispatcher().dispatch(event, action, MSG)
        assert r.attempted is True
        assert r.delivered is True
        assert r.reference

    @pytest.mark.parametrize("action", [
        PolicyAction.IMMEDIATE_RETRY,
        PolicyAction.SCHEDULED_RETRY,
    ])
    def test_silent_retries_are_not_contact(self, event, action):
        """A retry against the rail must never inflate the contacted count."""
        r = SimulatedOutreachDispatcher().dispatch(event, action, MSG)
        assert r.attempted is False
        assert r.delivered is False
        assert r.channel is OutreachChannel.NONE


class TestComplianceGate:
    def test_nothing_is_sent_without_a_compliant_draft(self, event):
        # The reasoning layer withholds copy that names amounts or promises
        # an outcome; there is then nothing safe to send.
        r = SimulatedOutreachDispatcher().dispatch(
            event, PolicyAction.TRIGGER_REAUTHORIZATION, None
        )
        assert r.attempted is True
        assert r.delivered is False
        assert r.message is None

    def test_only_the_drafted_copy_is_sent(self, event):
        r = SimulatedOutreachDispatcher().dispatch(
            event, PolicyAction.TRIGGER_REAUTHORIZATION, MSG
        )
        assert r.message == MSG


class TestHonesty:
    def test_every_result_is_flagged_simulated(self, event):
        for action in (PolicyAction.TRIGGER_REAUTHORIZATION, PolicyAction.IMMEDIATE_RETRY):
            assert SimulatedOutreachDispatcher().dispatch(event, action, MSG).simulated

    def test_delivery_is_logged_for_verification(self, event):
        d = SimulatedOutreachDispatcher()
        d.dispatch(event, PolicyAction.TRIGGER_REAUTHORIZATION, MSG)
        assert len(d.sent_log) == 1
        assert d.sent_log[0]["payment_id"] == "pay_out"
