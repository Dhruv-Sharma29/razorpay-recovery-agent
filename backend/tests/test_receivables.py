"""Overdue-receivable (B2B chaser) tests.

A second revenue-risk surface reusing the same guarantees: bounded
reminder count, a cooldown so chasing is not harassment, escalation to a
human when reminders run out, and the same amount cap.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.classifier.engine import FailureClassifier
from app.classifier.result import FailureCategory
from app.outreach import SimulatedOutreachDispatcher
from app.models.payment_event import FailedTransactionEvent
from app.policy.engine import RecoveryPolicyEngine
from app.policy.result import PolicyAction

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def _invoice(code="INVOICE_OVERDUE", amount=400000, attempt=1):
    return FailedTransactionEvent(
        event_id=f"evt_inv_{attempt}_{amount}", razorpay_payment_id=f"pay_inv_{attempt}_{amount}",
        merchant_id="merch_b2b", customer_id="cust_b2b", type="one_time",
        amount=amount, currency="INR", payment_method="netbanking",
        error_code=code, error_description="Invoice past its due date",
        failure_category="unknown", attempt_number=attempt,
        mandate_status=None, timestamp=T0,
    )


def _decide(event):
    cls = FailureClassifier().classify(event)
    return cls, RecoveryPolicyEngine().evaluate(event, cls)


class TestClassification:
    @pytest.mark.parametrize("code", ["INVOICE_OVERDUE", "PAYMENT_OVERDUE"])
    def test_overdue_codes_classify_as_receivables(self, code):
        cls, _ = _decide(_invoice(code))
        assert cls.category is FailureCategory.OVERDUE_RECEIVABLE
        assert cls.rule_id == "code.overdue_receivable"

    def test_it_is_not_confused_with_a_payment_failure(self):
        cls, _ = _decide(_invoice())
        assert cls.category is not FailureCategory.INSUFFICIENT_FUNDS
        assert cls.category is not FailureCategory.UNKNOWN


class TestPolicy:
    def test_first_overdue_invoice_gets_a_reminder(self):
        _, d = _decide(_invoice())
        assert d.action is PolicyAction.SEND_PAYMENT_REMINDER
        assert d.automatic_recovery_allowed is True

    def test_reminders_wait_72h_so_chasing_is_not_harassment(self):
        _, d = _decide(_invoice())
        assert d.cooldown_seconds == 259_200

    def test_reminders_are_capped(self):
        _, d = _decide(_invoice())
        assert d.max_retries_for_category == 3

    def test_escalates_to_a_human_once_reminders_run_out(self):
        _, d = _decide(_invoice(attempt=5))
        assert d.automatic_recovery_allowed is False
        assert d.action is PolicyAction.ESCALATE

    def test_the_amount_cap_still_applies(self):
        """A large invoice is a human's decision, not the agent's."""
        _, d = _decide(_invoice(amount=9_000_000))
        assert d.automatic_recovery_allowed is False


class TestOutreach:
    def test_a_reminder_actually_contacts_the_customer(self):
        r = SimulatedOutreachDispatcher().dispatch(
            _invoice(), PolicyAction.SEND_PAYMENT_REMINDER,
            "Your invoice is past its due date. Please arrange payment.",
        )
        assert r.attempted is True
        assert r.delivered is True
