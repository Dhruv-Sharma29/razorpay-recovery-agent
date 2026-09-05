"""Razorpay credential check.

The endpoint exists to separate three failures that otherwise look alike:
bad credentials, no network, and a correctly-refused missing mandate. These
tests pin that each is reported distinctly, and that the secret never leaks
into a response that is safe to screenshot.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.razorpay.health import check_credentials

client = TestClient(app)

SECRET = "super_secret_value_98765"


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_ABCDEF123456")
    monkeypatch.setattr(settings, "razorpay_key_secret", SECRET)


def _response(status: int, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    return r


class TestBeforeAnyNetworkCall:
    def test_missing_credentials_say_what_to_set(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "razorpay_key_id", "")
        monkeypatch.setattr(settings, "razorpay_key_secret", "")
        with patch("app.razorpay.health.httpx.get") as get:
            result = check_credentials()
        get.assert_not_called()
        assert result["status"] == "not_configured"
        assert "RAZORPAY_KEY_ID" in result["detail"]

    def test_a_live_key_is_refused_without_contacting_razorpay(
        self, monkeypatch
    ) -> None:
        """A live key moves real money; it must never be probed casually."""
        monkeypatch.setattr(settings, "razorpay_key_id", "rzp_live_DANGER")
        monkeypatch.setattr(settings, "razorpay_key_secret", SECRET)
        with patch("app.razorpay.health.httpx.get") as get:
            result = check_credentials()
        get.assert_not_called()
        assert result["status"] == "live_key_refused"
        assert result["test_mode"] is False


class TestOutcomesAreDistinct:
    def test_valid_credentials_report_ok(self, configured) -> None:
        with patch(
            "app.razorpay.health.httpx.get",
            return_value=_response(200, {"count": 3}),
        ):
            result = check_credentials()
        assert result["status"] == "ok"
        assert result["reachable"] is True
        assert result["payments_visible"] == 3

    def test_a_fresh_account_with_no_payments_still_passes(self, configured) -> None:
        with patch(
            "app.razorpay.health.httpx.get",
            return_value=_response(200, {"count": 0}),
        ):
            result = check_credentials()
        assert result["status"] == "ok"

    def test_rejected_credentials_are_not_reported_as_a_network_fault(
        self, configured
    ) -> None:
        with patch(
            "app.razorpay.health.httpx.get", return_value=_response(401)
        ):
            result = check_credentials()
        assert result["status"] == "unauthorized"
        # Razorpay answered, so the network is fine — that distinction is the
        # entire point of this endpoint.
        assert result["reachable"] is True

    def test_a_timeout_is_reported_as_a_network_fault(self, configured) -> None:
        with patch(
            "app.razorpay.health.httpx.get",
            side_effect=httpx.TimeoutException("slow"),
        ):
            result = check_credentials()
        assert result["status"] == "unreachable"
        assert "network" in result["detail"].lower()

    def test_a_connection_failure_says_credentials_were_never_tested(
        self, configured
    ) -> None:
        with patch(
            "app.razorpay.health.httpx.get",
            side_effect=httpx.ConnectError("refused"),
        ):
            result = check_credentials()
        assert result["status"] == "unreachable"
        assert "never tested" in result["detail"]

    def test_an_unexpected_error_never_escapes(self, configured) -> None:
        with patch(
            "app.razorpay.health.httpx.get", side_effect=RuntimeError("boom")
        ):
            result = check_credentials()
        assert result["status"] == "error"


class TestItSaysWhetherCallsAreLive:
    def test_mock_mode_is_called_out_even_when_keys_work(
        self, configured, monkeypatch
    ) -> None:
        """Working keys with EXECUTOR_MODE=mock still means nothing is sent."""
        monkeypatch.setattr(settings, "executor_mode", "mock")
        with patch(
            "app.razorpay.health.httpx.get",
            return_value=_response(200, {"count": 0}),
        ):
            result = check_credentials()
        assert result["live_calls_enabled"] is False
        assert "still" in result["detail"]

    def test_live_mode_is_reported_as_enabled(self, configured, monkeypatch) -> None:
        monkeypatch.setattr(settings, "executor_mode", "razorpay_test")
        with patch(
            "app.razorpay.health.httpx.get",
            return_value=_response(200, {"count": 0}),
        ):
            result = check_credentials()
        assert result["live_calls_enabled"] is True


class TestSecretSafety:
    def test_the_secret_never_appears_in_any_outcome(self, configured) -> None:
        """The response should be safe to paste into a bug report."""
        cases = [
            _response(200, {"count": 1}),
            _response(401),
            _response(500),
        ]
        for response in cases:
            with patch("app.razorpay.health.httpx.get", return_value=response):
                assert SECRET not in str(check_credentials())
        for error in (httpx.TimeoutException("x"), httpx.ConnectError("x")):
            with patch("app.razorpay.health.httpx.get", side_effect=error):
                assert SECRET not in str(check_credentials())

    def test_the_key_id_is_masked(self, configured) -> None:
        with patch(
            "app.razorpay.health.httpx.get",
            return_value=_response(200, {"count": 0}),
        ):
            result = check_credentials()
        assert result["key_id"] != "rzp_test_ABCDEF123456"
        assert result["key_id"].startswith("rzp_test_")


class TestEndpoint:
    def test_the_endpoint_answers_without_credentials(self) -> None:
        body = client.get("/api/dashboard/razorpay-check").json()
        assert "status" in body
        assert "detail" in body

    def test_the_endpoint_leaks_no_secret_value(self, monkeypatch) -> None:
        """Naming the variable in guidance is fine; printing its value is not."""
        monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_ABCDEF123456")
        monkeypatch.setattr(settings, "razorpay_key_secret", SECRET)
        with patch(
            "app.razorpay.health.httpx.get",
            return_value=_response(200, {"count": 0}),
        ):
            body = client.get("/api/dashboard/razorpay-check").json()
        assert SECRET not in str(body)
