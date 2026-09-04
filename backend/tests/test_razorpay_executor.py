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

    def test_test_key_accepted(self, payment_event, policy_allowed):
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
                    result = executor.execute(payment_event, policy_allowed)

        mock_client.post.assert_called_once()
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

    def test_at_cap_accepted(self, payment_event, policy_allowed):
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

        at_cap_event = payment_event.model_copy(update={"amount": 500000})

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", "test_secret"):
                with patch.object(settings, "auto_recovery_amount_limit", 500000):
                    with patch.object(executor, "_client", mock_client):
                        result = executor.execute(at_cap_event, policy_allowed)

        mock_client.post.assert_called_once()
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

    def test_http_error(self, payment_event, policy_allowed):
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
                    result = executor.execute(payment_event, policy_allowed)

        assert result.status == ExecutionStatus.FAILED
        assert "HTTP 400" in result.error

    def test_timeout(self, payment_event, policy_allowed):
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.TimeoutException("timed out")

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", "test_secret"):
                with patch.object(executor, "_client", mock_client):
                    result = executor.execute(payment_event, policy_allowed)

        assert result.status == ExecutionStatus.FAILED
        assert "timed out" in result.error

    def test_connect_error(self, payment_event, policy_allowed):
        executor = RazorpayTestExecutor()
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")

        with patch.object(settings, "razorpay_key_id", "rzp_test_ABCDEF"):
            with patch.object(settings, "razorpay_key_secret", "test_secret"):
                with patch.object(executor, "_client", mock_client):
                    result = executor.execute(payment_event, policy_allowed)

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
