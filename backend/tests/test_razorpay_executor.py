"""Tests for the RazorpayTestExecutor (Phase 3.6).

All tests mock the HTTP client — no real Razorpay calls are made.

Coverage:
  - Non-test key prefix is refused before any HTTP call.
  - Amount over cap is refused before any HTTP call.
  - Policy-denied decision never reaches _do_execute (base class guarantee).
  - Successful capture returns ExecOutcome with status=captured.
  - HTTP errors, timeouts, and connection failures return structured failures.
  - Credentials never appear in error messages or logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.config import settings
from app.executor.base import ExecOutcome
from app.executor.result import ExecutionStatus
from app.models.payment_event import (
    FailedTransactionEvent,
    RazorpayContext,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.policy.result import PolicyAction, PolicyDecision
from app.razorpay.executor import RazorpayTestExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def payment_event() -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id="evt_rzp_test_001",
        razorpay_payment_id="pay_test_rzp_001",
        merchant_id="merch_01",
        customer_id="cust_01",
        type=TransactionType.ONE_TIME,
        amount=149900,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code="INSUFFICIENT_FUNDS",
        error_description="Payment failed due to insufficient funds",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        attempt_number=1,
        mandate_status=None,
        timestamp=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture()
def mandate_event(payment_event) -> FailedTransactionEvent:
    """The same event, but with a saved mandate on file.

    Charging unattended is only lawful with a token, so any test that needs
    to reach the network has to supply one.
    """
    return payment_event.model_copy(
        update={
            "razorpay": RazorpayContext(
                customer_id="cust_test_01",
                token_id="token_test_01",
                email="customer@example.com",
                contact="+919999999999",
            )
        }
    )


@pytest.fixture()
def policy_allowed() -> PolicyDecision:
    return PolicyDecision(
        action=PolicyAction.SCHEDULED_RETRY,
        automatic_recovery_allowed=True,
        reason="Insufficient funds: retry permitted",
        rule_id="policy.insufficient_funds.retry_24h",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        escalation_required=False,
        escalation_reason=None,
        max_retries_for_category=2,
        current_attempt=1,
        amount=149900,
        amount_limit=500000,
    )


@pytest.fixture()
def policy_denied() -> PolicyDecision:
    return PolicyDecision(
        action=PolicyAction.ESCALATE,
        automatic_recovery_allowed=False,
        reason="Retry limit exhausted",
        rule_id="policy.insufficient_funds.escalate",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        escalation_required=True,
        escalation_reason=None,
        max_retries_for_category=2,
        current_attempt=3,
        amount=149900,
        amount_limit=500000,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKeyPrefixGuard:
    """Non-test keys must be refused before any HTTP call."""

    def test_live_key_refused(self, payment_event, policy_allowed):
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()

        with patch.object(settings, "razorpay_key_id", "rzp_live_ABCDEF"):
            with patch.object(executor, "_client", mock_client):
                result = executor.execute(payment_event, policy_allowed)

        # No HTTP call was made
        mock_client.post.assert_not_called()
        assert result.status == ExecutionStatus.FAILED
        assert "rzp_test_" in result.error

    def test_empty_key_refused(self, payment_event, policy_allowed):
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()

        with patch.object(settings, "razorpay_key_id", ""):
            with patch.object(executor, "_client", mock_client):
                result = executor.execute(payment_event, policy_allowed)

        mock_client.post.assert_not_called()
        assert result.status == ExecutionStatus.FAILED

    def test_test_key_accepted(self, mandate_event, policy_allowed):
        """A test key should pass the prefix guard (HTTP call may fail,
        but the guard itself shouldn't block it)."""
        executor = RazorpayTestExecutor()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "pay_test_captured",
            "status": "captured",
            "amount": 149900,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", "test_secret"):
                with patch.object(executor, "_client", mock_client):
                    result = executor.execute(mandate_event, policy_allowed)

        # A recovery charge is two calls: a fresh order, then the debit.
        assert mock_client.post.call_count == 2
        paths = [call.args[0] for call in mock_client.post.call_args_list]
        assert paths[0].endswith("/orders")
        assert paths[1].endswith("/payments/create/recurring")
        assert result.status == ExecutionStatus.SUCCESS


class TestAmountCapGuard:
    """Amount over cap must be refused before any HTTP call."""

    def test_over_cap_refused(self, payment_event, policy_allowed):
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()

        # Set amount above cap
        over_cap_event = payment_event.model_copy(update={"amount": 600000})

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "auto_recovery_amount_limit", 500000):
                with patch.object(executor, "_client", mock_client):
                    result = executor.execute(over_cap_event, policy_allowed)

        mock_client.post.assert_not_called()
        assert result.status == ExecutionStatus.FAILED
        assert "exceeds" in result.error

    def test_at_cap_accepted(self, mandate_event, policy_allowed):
        """Exactly at the cap should be accepted."""
        executor = RazorpayTestExecutor()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "pay_test_at_cap",
            "status": "captured",
            "amount": 500000,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        at_cap_event = mandate_event.model_copy(update={"amount": 500000})

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", "test_secret"):
                with patch.object(settings, "auto_recovery_amount_limit", 500000):
                    with patch.object(executor, "_client", mock_client):
                        result = executor.execute(at_cap_event, policy_allowed)

        # Still two calls at the cap: the guard permits, it does not reshape.
        assert mock_client.post.call_count == 2
        assert result.status == ExecutionStatus.SUCCESS


class TestPolicyDeniedNeverExecutes:
    """Policy-denied decisions must never reach _do_execute.

    This is guaranteed by the base class, but we verify it hasn't been
    accidentally bypassed.
    """

    def test_policy_denied_rejected(self, payment_event, policy_denied):
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()

        with patch.object(executor, "_client", mock_client):
            result = executor.execute(payment_event, policy_denied)

        mock_client.post.assert_not_called()
        assert result.status == ExecutionStatus.REJECTED
        assert result.executed is False


class TestHttpErrorHandling:
    """Razorpay API errors must produce structured failures."""

    def test_http_error(self, mandate_event, policy_allowed):
        executor = RazorpayTestExecutor()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {}
        mock_response.stream = MagicMock()
        error = httpx.HTTPStatusError(
            "Bad Request",
            request=MagicMock(),
            response=mock_response,
        )

        mock_client = MagicMock()
        mock_client.post.side_effect = error

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", "test_secret"):
                with patch.object(executor, "_client", mock_client):
                    result = executor.execute(mandate_event, policy_allowed)

        assert result.status == ExecutionStatus.FAILED
        assert "HTTP 400" in result.error

    def test_timeout(self, mandate_event, policy_allowed):
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.TimeoutException("timed out")

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", "test_secret"):
                with patch.object(executor, "_client", mock_client):
                    result = executor.execute(mandate_event, policy_allowed)

        assert result.status == ExecutionStatus.FAILED
        assert "timed out" in result.error

    def test_connect_error(self, mandate_event, policy_allowed):
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", "test_secret"):
                with patch.object(executor, "_client", mock_client):
                    result = executor.execute(mandate_event, policy_allowed)

        assert result.status == ExecutionStatus.FAILED
        assert "connect" in result.error.lower()


class TestCredentialSafety:
    """Credentials must never appear in error messages or results."""

    def test_secret_not_in_error(self, payment_event, policy_allowed):
        secret = "super_secret_key_12345"
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.TimeoutException("timed out")

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", secret):
                with patch.object(executor, "_client", mock_client):
                    result = executor.execute(payment_event, policy_allowed)

        assert secret not in (result.error or "")
        assert secret not in (result.reason or "")


# ---------------------------------------------------------------------------
# Action routing
#
# A failed payment is terminal at Razorpay, so recovery is always a NEW
# attempt. Which kind depends on the policy's action, and the executor must
# never improvise one action into another.
# ---------------------------------------------------------------------------


def _decision(action: PolicyAction) -> PolicyDecision:
    return PolicyDecision(
        action=action,
        automatic_recovery_allowed=True,
        reason="Recovery permitted for test",
        rule_id=f"policy.test.{action.value}",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        escalation_required=False,
        escalation_reason=None,
        max_retries_for_category=2,
        current_attempt=1,
        amount=149900,
        amount_limit=500000,
    )


def _ok(payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def _run(executor, event, decision, responses):
    mock_client = MagicMock()
    mock_client.post.side_effect = responses
    with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
        with patch.object(settings, "razorpay_key_secret", "test_secret"):
            with patch.object(executor, "_client", mock_client):
                return executor.execute(event, decision), mock_client


class TestNeverCaptures:
    """Capture only applies to authorized payments, never to failed ones."""

    def test_no_action_ever_calls_the_capture_endpoint(self, mandate_event):
        for action in (
            PolicyAction.SCHEDULED_RETRY,
            PolicyAction.IMMEDIATE_RETRY,
            PolicyAction.SEND_PAYMENT_REMINDER,
            PolicyAction.SWITCH_PAYMENT_METHOD,
            PolicyAction.TRIGGER_REAUTHORIZATION,
            PolicyAction.RESEND_AUTH_PROMPT,
        ):
            executor = RazorpayTestExecutor()
            _, client = _run(
                executor,
                mandate_event,
                _decision(action),
                [
                    _ok({"id": "order_1", "status": "created"}),
                    _ok({"id": "pay_1", "status": "captured"}),
                ],
            )
            paths = [call.args[0] for call in client.post.call_args_list]
            assert not [p for p in paths if "capture" in p], action


class TestChargeRouting:
    """Unattended actions charge a saved mandate against a fresh order."""

    def test_retry_creates_a_new_order_before_debiting(self, mandate_event):
        executor = RazorpayTestExecutor()
        result, client = _run(
            executor,
            mandate_event,
            _decision(PolicyAction.SCHEDULED_RETRY),
            [
                _ok({"id": "order_new", "status": "created"}),
                _ok({"razorpay_payment_id": "pay_new", "status": "captured"}),
            ],
        )
        paths = [call.args[0] for call in client.post.call_args_list]
        assert paths[0].endswith("/orders")
        assert paths[1].endswith("/payments/create/recurring")
        # The debit must be tied to the order just created, not the failed one.
        assert client.post.call_args_list[1].kwargs["json"]["order_id"] == "order_new"
        assert result.amount_recovered == 149900

    def test_the_debit_carries_the_saved_token_and_customer(self, mandate_event):
        executor = RazorpayTestExecutor()
        _, client = _run(
            executor,
            mandate_event,
            _decision(PolicyAction.IMMEDIATE_RETRY),
            [
                _ok({"id": "order_new"}),
                _ok({"razorpay_payment_id": "pay_new", "status": "captured"}),
            ],
        )
        body = client.post.call_args_list[1].kwargs["json"]
        assert body["token"] == "token_test_01"
        assert body["customer_id"] == "cust_test_01"
        assert body["recurring"] == "1"

    def test_a_queued_debit_is_not_counted_as_money_recovered(self, mandate_event):
        """File-based issuers park the payment in `created` for hours."""
        executor = RazorpayTestExecutor()
        result, _ = _run(
            executor,
            mandate_event,
            _decision(PolicyAction.SCHEDULED_RETRY),
            [
                _ok({"id": "order_new"}),
                _ok({"razorpay_payment_id": "pay_new", "status": "created"}),
            ],
        )
        assert result.executed is True
        assert result.amount_recovered == 0
        assert result.payment_status == "created"


class TestLinkRouting:
    """Customer-present actions become a payment link Razorpay delivers."""

    def test_reminder_creates_a_payment_link(self, mandate_event):
        executor = RazorpayTestExecutor()
        result, client = _run(
            executor,
            mandate_event,
            _decision(PolicyAction.SEND_PAYMENT_REMINDER),
            [_ok({"id": "plink_1", "short_url": "https://rzp.io/i/x", "status": "created"})],
        )
        assert client.post.call_count == 1
        assert client.post.call_args.args[0].endswith("/payment_links")
        assert result.executed is True

    def test_razorpay_is_asked_to_deliver_the_link(self, mandate_event):
        executor = RazorpayTestExecutor()
        _, client = _run(
            executor,
            mandate_event,
            _decision(PolicyAction.SWITCH_PAYMENT_METHOD),
            [_ok({"id": "plink_1", "status": "created"})],
        )
        body = client.post.call_args.kwargs["json"]
        assert body["notify"] == {"sms": True, "email": True}
        assert body["customer"]["email"] == "customer@example.com"

    def test_sending_a_link_recovers_no_money_yet(self, mandate_event):
        """The customer has been asked to pay. They have not paid."""
        executor = RazorpayTestExecutor()
        result, _ = _run(
            executor,
            mandate_event,
            _decision(PolicyAction.RESEND_AUTH_PROMPT),
            [_ok({"id": "plink_1", "status": "created"})],
        )
        assert result.amount_recovered == 0
        assert result.payment_status == "link_sent"

    def test_the_reference_id_fits_razorpays_forty_character_limit(
        self, mandate_event
    ):
        executor = RazorpayTestExecutor()
        _, client = _run(
            executor,
            mandate_event,
            _decision(PolicyAction.SEND_PAYMENT_REMINDER),
            [_ok({"id": "plink_1"})],
        )
        reference = client.post.call_args.kwargs["json"]["reference_id"]
        assert 0 < len(reference) <= 40


class TestMissingPrerequisites:
    """A gap in the integration must never look like a declined payment."""

    def test_a_retry_without_a_mandate_is_not_attempted(
        self, payment_event, policy_allowed
    ):
        executor = RazorpayTestExecutor()
        result, client = _run(executor, payment_event, policy_allowed, [])
        client.post.assert_not_called()
        assert result.payment_status == "not_attempted"
        assert "mandate" in result.error.lower()

    def test_a_link_without_any_contact_is_not_attempted(self, payment_event):
        executor = RazorpayTestExecutor()
        result, client = _run(
            executor, payment_event, _decision(PolicyAction.SEND_PAYMENT_REMINDER), []
        )
        client.post.assert_not_called()
        assert result.payment_status == "not_attempted"

    def test_a_link_needs_only_one_channel(self, payment_event):
        """An email address alone is enough to reach the customer."""
        executor = RazorpayTestExecutor()
        event = payment_event.model_copy(
            update={"razorpay": RazorpayContext(email="only@example.com")}
        )
        result, client = _run(
            executor,
            event,
            _decision(PolicyAction.SEND_PAYMENT_REMINDER),
            [_ok({"id": "plink_1"})],
        )
        assert client.post.call_count == 1
        assert client.post.call_args.kwargs["json"]["notify"] == {
            "sms": False,
            "email": True,
        }
        assert result.executed is True

    def test_not_attempted_is_distinguishable_from_a_real_decline(
        self, mandate_event, payment_event, policy_allowed
    ):
        """The whole point: these two must not look alike."""
        declined, _ = _run(
            RazorpayTestExecutor(),
            mandate_event,
            policy_allowed,
            [_ok({"id": "order_1"}), _ok({"status": "failed"})],
        )
        blocked, _ = _run(
            RazorpayTestExecutor(), payment_event, policy_allowed, []
        )
        assert declined.payment_status == "failed"
        assert blocked.payment_status == "not_attempted"


class TestHonestyFlag:
    """A real gateway call must not be labelled simulated."""

    def test_a_real_call_is_not_marked_simulated(self, mandate_event):
        executor = RazorpayTestExecutor()
        result, _ = _run(
            executor,
            mandate_event,
            _decision(PolicyAction.SEND_PAYMENT_REMINDER),
            [_ok({"id": "plink_1"})],
        )
        assert result.simulated is False
