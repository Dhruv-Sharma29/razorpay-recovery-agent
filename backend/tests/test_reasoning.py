"""Tests for the Qwen 3.5 via NIM reasoning layer (TASK-005).

All unit tests mock HTTP interactions — no running NIM instance required.
An optional integration test (marked ``@pytest.mark.integration``) connects
to a real NIM server and is skipped when one is not available.

Test coverage:
  1.  Successful NIM response
  2.  Valid structured reasoning response
  3.  NIM unavailable (connection refused)
  4.  Timeout
  5.  Malformed response (unparseable JSON)
  6.  Invalid structured output (missing fields)
  7.  Reasoning cannot override a policy denial
  8.  Reasoning cannot change the policy decision
  9.  Policy-approved action remains represented correctly
  10. Policy-denied action remains denied even when Qwen recommends recovery
  11. No mutation of the payment event
  12. Deterministic fallback behavior
  13. Configured model name is used
  14. Configured NIM URL is used
  15. No real NIM server is required for unit tests

Plus:
  - Invalid confidence values (out of range, non-numeric)
  - HTTP error statuses (500, 404)
  - Markdown-fenced JSON from model
  - Optional integration test (skipped when NIM is unavailable)
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.classifier.result import ClassificationCertainty, ClassificationResult
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.policy.result import EscalationReason, PolicyAction, PolicyDecision
from app.reasoning.engine import RecoveryReasoner, _build_fallback, _parse_nim_response
from app.reasoning.result import ReasoningResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_NIM_URL = "http://test-nim:11434"
_TEST_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"


@pytest.fixture()
def payment_event() -> FailedTransactionEvent:
    """A typical insufficient-funds payment event."""
    return FailedTransactionEvent(
        event_id="evt_test_001",
        razorpay_payment_id="pay_test_abc",
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
def classification() -> ClassificationResult:
    """Classification result for insufficient funds."""
    return ClassificationResult(
        category=FailureCategory.INSUFFICIENT_FUNDS,
        confidence=1.0,
        certainty=ClassificationCertainty.HIGH,
        reason="Error code INSUFFICIENT_FUNDS maps to insufficient_funds",
        rule_id="code.insufficient_funds",
        source_field="error_code",
    )


@pytest.fixture()
def policy_allowed() -> PolicyDecision:
    """Policy decision that ALLOWS automatic recovery."""
    return PolicyDecision(
        action=PolicyAction.SCHEDULED_RETRY,
        automatic_recovery_allowed=True,
        reason="Insufficient funds: retry after 24h cooldown permitted (attempt 1/2)",
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
    """Policy decision that DENIES automatic recovery (escalation)."""
    return PolicyDecision(
        action=PolicyAction.ESCALATE,
        automatic_recovery_allowed=False,
        reason="Retry limit exhausted for insufficient_funds: attempt 3 exceeds maximum 2 retries",
        rule_id="policy.insufficient_funds.retry_limit_exhausted",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        escalation_required=True,
        escalation_reason=EscalationReason.RETRY_LIMIT_EXHAUSTED,
        max_retries_for_category=2,
        current_attempt=3,
        amount=149900,
        amount_limit=500000,
    )


@pytest.fixture()
def reasoner() -> RecoveryReasoner:
    """RecoveryReasoner configured with test URL and model."""
    return RecoveryReasoner(
        nim_api_key="test-key-123",
        nim_base_url=_TEST_NIM_URL,
        nim_model=_TEST_MODEL,
        timeout=5.0,
    )


def _mock_nim_success(
    recommendation: str = "Retry the payment after a 24h cooldown period",
    explanation: str = (
        "The payment failed because the customer's account had insufficient "
        "funds. This is within the permitted retry limit (attempt 1 of 2) "
        "and below the automatic-recovery threshold."
    ),
    confidence: float = 0.92,
) -> dict[str, Any]:
    """Build a mock NIM /chat/completions response body."""
    content = json.dumps(
        {
            "recommendation": recommendation,
            "explanation": explanation,
            "confidence": confidence,
        }
    )
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1677652288,
        "model": _TEST_MODEL,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
            },
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21}
    }


def _make_httpx_response(
    body: dict[str, Any],
    status_code: int = 200,
) -> httpx.Response:
    """Create a fake httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("POST", f"{_TEST_NIM_URL}/chat/completions"),
    )
    return resp


# ---------------------------------------------------------------------------
# 1. Successful NIM response
# ---------------------------------------------------------------------------


class TestSuccessfulResponse:
    def test_successful_nim_call(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """NIM returns valid JSON → success=True, is_fallback=False."""
        mock_body = _mock_nim_success()
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is True
        assert result.is_fallback is False
        assert result.error is None
        assert result.model_id == _TEST_MODEL

    # ------------------------------------------------------------------
    # 2. Valid structured reasoning response
    # ------------------------------------------------------------------

    def test_valid_structured_fields(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Result contains recommendation, explanation, confidence from model."""
        mock_body = _mock_nim_success(
            recommendation="Schedule a retry",
            explanation="Funds were insufficient.",
            confidence=0.85,
        )
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.recommendation == "Schedule a retry"
        assert result.explanation == "Funds were insufficient."
        assert result.confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# 3. NIM unavailable
# ---------------------------------------------------------------------------


class TestNIMUnavailable:
    def test_connection_refused(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Connection refused → fallback, no crash."""
        with patch.object(
            httpx,
            "post",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True
        assert "connect" in result.error.lower() or "Connect" in result.error
        # Policy decision preserved
        assert result.policy_action_allowed is True
        assert "insufficient_funds" in result.explanation
        assert "retry after 24h cooldown" in result.explanation

    def test_generic_network_error(
        self, reasoner, payment_event, classification, policy_denied
    ):
        """Generic exception → fallback, policy denial preserved."""
        with patch.object(
            httpx,
            "post",
            side_effect=Exception("network glitch"),
        ):
            result = reasoner.analyze(payment_event, classification, policy_denied)

        assert result.success is False
        assert result.is_fallback is True
        assert result.policy_action_allowed is False


# ---------------------------------------------------------------------------
# 4. Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_returns_fallback(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Timeout → fallback, no crash, policy preserved."""
        with patch.object(
            httpx,
            "post",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True
        assert "timed out" in result.error.lower() or "timeout" in result.error.lower()


# ---------------------------------------------------------------------------
# 5. Malformed response
# ---------------------------------------------------------------------------


class TestMalformedResponse:
    def test_non_json_content(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Model returns non-JSON text → fallback."""
        body = {"choices": [{"message": {"role": "assistant", "content": "I cannot help you"}}]}
        with patch.object(httpx, "post", return_value=_make_httpx_response(body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True
        assert "malformed" in result.error.lower() or "Malformed" in result.error

    def test_empty_content(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Model returns empty content → fallback."""
        body = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
        with patch.object(httpx, "post", return_value=_make_httpx_response(body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True

    def test_markdown_fenced_json_is_parsed(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Model wraps JSON in ```json fencing → still parsed correctly."""
        inner = json.dumps(
            {
                "recommendation": "Retry after cooldown",
                "explanation": "Insufficient funds detected.",
                "confidence": 0.9,
            }
        )
        body = {"choices": [{"message": {"role": "assistant", "content": f"```json\n{inner}\n```"}}]}
        with patch.object(httpx, "post", return_value=_make_httpx_response(body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is True
        assert result.recommendation == "Retry after cooldown"


# ---------------------------------------------------------------------------
# 6. Invalid structured output
# ---------------------------------------------------------------------------


class TestInvalidStructuredOutput:
    def test_missing_recommendation(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """JSON present but 'recommendation' missing → fallback."""
        body = {"choices": [{"message": {"role": "assistant", "content": json.dumps({"explanation": "Some text", "confidence": 0.8})}}]}
        with patch.object(httpx, "post", return_value=_make_httpx_response(body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True
        assert "recommendation" in result.error.lower()

    def test_missing_explanation(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """JSON present but 'explanation' missing → fallback."""
        body = {"choices": [{"message": {"role": "assistant", "content": json.dumps({"recommendation": "Retry", "confidence": 0.8})}}]}
        with patch.object(httpx, "post", return_value=_make_httpx_response(body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True
        assert "explanation" in result.error.lower()

    def test_confidence_out_of_range(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Confidence > 1.0 → fallback."""
        body = {"choices": [{"message": {"role": "assistant", "content": json.dumps({"recommendation": "Retry", "explanation": "Reason", "confidence": 1.5})}}]}
        with patch.object(httpx, "post", return_value=_make_httpx_response(body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True
        assert "confidence" in result.error.lower()

    def test_confidence_non_numeric(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Confidence is a string → fallback."""
        body = {"choices": [{"message": {"role": "assistant", "content": json.dumps({"recommendation": "Retry", "explanation": "Reason", "confidence": "high"})}}]}
        with patch.object(httpx, "post", return_value=_make_httpx_response(body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True


# ---------------------------------------------------------------------------
# 7. Reasoning cannot override a policy denial
# ---------------------------------------------------------------------------


class TestReasoningCannotOverridePolicy:
    def test_denied_stays_denied_even_with_model_recommendation(
        self,
        reasoner,
        payment_event,
        classification,
        policy_denied,
    ):
        """Even if Qwen says 'retry', policy_action_allowed stays False."""
        mock_body = _mock_nim_success(
            recommendation="Retry immediately — the customer likely has funds now",
            explanation="The customer should have funds now.",
            confidence=0.95,
        )
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            result = reasoner.analyze(payment_event, classification, policy_denied)

        assert result.success is True
        assert result.policy_action_allowed is False
        # The model may recommend whatever it wants textually, but the
        # authoritative flag remains False.


# ---------------------------------------------------------------------------
# 8. Reasoning cannot change the policy decision
# ---------------------------------------------------------------------------


class TestReasoningCannotChangeDecision:
    def test_policy_fields_are_not_mutated(
        self,
        reasoner,
        payment_event,
        classification,
        policy_denied,
    ):
        """The policy decision object is unchanged after reasoning."""
        original = policy_denied.model_copy()
        mock_body = _mock_nim_success()
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            reasoner.analyze(payment_event, classification, policy_denied)

        assert policy_denied == original

    def test_policy_allowed_fields_unchanged(
        self,
        reasoner,
        payment_event,
        classification,
        policy_allowed,
    ):
        """Policy-allowed decision is unchanged after reasoning."""
        original = policy_allowed.model_copy()
        mock_body = _mock_nim_success()
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            reasoner.analyze(payment_event, classification, policy_allowed)

        assert policy_allowed == original


# ---------------------------------------------------------------------------
# 9. Policy-approved action represented correctly
# ---------------------------------------------------------------------------


class TestPolicyApprovedCorrect:
    def test_allowed_action_reflected(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """When policy allows, result.policy_action_allowed is True."""
        mock_body = _mock_nim_success()
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.policy_action_allowed is True
        assert result.success is True

    def test_allowed_action_on_fallback(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Even on fallback, allowed policy is still represented as allowed."""
        with patch.object(
            httpx, "post", side_effect=httpx.ConnectError("refused")
        ):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.policy_action_allowed is True
        assert result.is_fallback is True


# ---------------------------------------------------------------------------
# 10. Policy-denied action stays denied even when Qwen recommends
# ---------------------------------------------------------------------------


class TestPolicyDeniedStaysDenied:
    def test_denied_on_success(
        self, reasoner, payment_event, classification, policy_denied
    ):
        """Successful Qwen call + policy denied → still denied."""
        mock_body = _mock_nim_success(
            recommendation="I strongly recommend retrying this payment",
        )
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            result = reasoner.analyze(payment_event, classification, policy_denied)

        assert result.policy_action_allowed is False

    def test_denied_on_fallback(
        self, reasoner, payment_event, classification, policy_denied
    ):
        """Fallback + policy denied → still denied."""
        with patch.object(
            httpx, "post", side_effect=httpx.TimeoutException("slow")
        ):
            result = reasoner.analyze(payment_event, classification, policy_denied)

        assert result.policy_action_allowed is False
        assert result.is_fallback is True


# ---------------------------------------------------------------------------
# 11. No mutation of the payment event
# ---------------------------------------------------------------------------


class TestNoMutationOfPaymentEvent:
    def test_payment_event_unchanged_on_success(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Payment event is not mutated during successful reasoning."""
        original = payment_event.model_copy(deep=True)
        mock_body = _mock_nim_success()
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            reasoner.analyze(payment_event, classification, policy_allowed)

        assert payment_event == original

    def test_payment_event_unchanged_on_fallback(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """Payment event is not mutated during fallback."""
        original = payment_event.model_copy(deep=True)
        with patch.object(
            httpx, "post", side_effect=httpx.ConnectError("refused")
        ):
            reasoner.analyze(payment_event, classification, policy_allowed)

        assert payment_event == original


# ---------------------------------------------------------------------------
# 12. Deterministic fallback behavior
# ---------------------------------------------------------------------------


class TestDeterministicFallback:
    def test_fallback_structure(self, policy_allowed):
        """_build_fallback produces the expected structure."""
        result = _build_fallback(policy_allowed, "test-model", "Test error")

        assert result.success is False
        assert result.is_fallback is True
        assert result.confidence == 0.0
        assert result.model_id == "test-model"
        assert result.error == "Test error"
        assert result.policy_action_allowed is True
        assert "scheduled_retry" in result.recommendation.lower()

    def test_fallback_denied_structure(self, policy_denied):
        """Fallback with denied policy preserves denial."""
        result = _build_fallback(policy_denied, "test-model", "Offline")

        assert result.policy_action_allowed is False
        assert result.is_fallback is True
        assert "escalate" in result.recommendation.lower()

    def test_fallback_is_consistent(self, policy_allowed):
        """Calling _build_fallback twice with the same inputs → same result."""
        r1 = _build_fallback(policy_allowed, "m", "err")
        r2 = _build_fallback(policy_allowed, "m", "err")
        assert r1 == r2


# ---------------------------------------------------------------------------
# 13. Configured model name is used
# ---------------------------------------------------------------------------


class TestConfiguredModel:
    def test_model_name_in_result(
        self, payment_event, classification, policy_allowed
    ):
        """Result.model_id matches the configured model."""
        custom_model = "qwen3.5:custom-fine-tuned"
        reasoner = RecoveryReasoner(
            nim_base_url=_TEST_NIM_URL,
            nim_model=custom_model,
        )
        mock_body = _mock_nim_success()
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.model_id == custom_model

    def test_model_name_in_payload(
        self, payment_event, classification, policy_allowed
    ):
        """The HTTP payload uses the configured model name."""
        custom_model = "qwen3.5:special"
        # An API key is required to reach the HTTP path at all: the engine
        # short-circuits to the deterministic fallback without one.
        reasoner = RecoveryReasoner(
            nim_api_key="test-key",
            nim_base_url=_TEST_NIM_URL,
            nim_model=custom_model,
        )
        mock_body = _mock_nim_success()
        with patch.object(
            httpx, "post", return_value=_make_httpx_response(mock_body)
        ) as mock_post:
            reasoner.analyze(payment_event, classification, policy_allowed)

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["model"] == custom_model

    def test_default_model_from_settings(self):
        """When no model is passed, settings.nim_model is used."""
        reasoner = RecoveryReasoner(nim_base_url=_TEST_NIM_URL)
        # The default is 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning' from config.py
        assert reasoner.model == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"


# ---------------------------------------------------------------------------
# 14. Configured NIM URL is used
# ---------------------------------------------------------------------------


class TestConfiguredUrl:
    def test_url_in_request(
        self, payment_event, classification, policy_allowed
    ):
        """HTTP request is sent to the configured NIM URL."""
        custom_url = "http://my-nim:9999"
        # See above: without a key the engine never issues the request.
        reasoner = RecoveryReasoner(
            nim_api_key="test-key",
            nim_base_url=custom_url,
            nim_model=_TEST_MODEL,
        )
        mock_body = _mock_nim_success()
        with patch.object(
            httpx, "post", return_value=_make_httpx_response(mock_body)
        ) as mock_post:
            reasoner.analyze(payment_event, classification, policy_allowed)

        call_args = mock_post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert url == f"{custom_url}/chat/completions"

    def test_default_url_from_settings(self):
        """When no URL is passed, settings.nim_base_url is used."""
        reasoner = RecoveryReasoner(nim_model=_TEST_MODEL)
        assert reasoner.base_url == "https://integrate.api.nvidia.com/v1"


# ---------------------------------------------------------------------------
# 15. No real NIM server required for unit tests (meta-test)
# ---------------------------------------------------------------------------


class TestNoRealServer:
    def test_all_unit_tests_use_mocks(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """This test verifies the pattern: mocked HTTP → no real server."""
        mock_body = _mock_nim_success()
        with patch.object(httpx, "post", return_value=_make_httpx_response(mock_body)):
            result = reasoner.analyze(payment_event, classification, policy_allowed)
        assert isinstance(result, ReasoningResult)


# ---------------------------------------------------------------------------
# Extra: HTTP error statuses
# ---------------------------------------------------------------------------


class TestHttpErrors:
    def test_500_server_error(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """HTTP 500 → fallback."""
        resp = _make_httpx_response({"error": "internal"}, status_code=500)
        with patch.object(httpx, "post", return_value=resp):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True
        assert "500" in result.error

    def test_404_not_found(
        self, reasoner, payment_event, classification, policy_allowed
    ):
        """HTTP 404 → fallback."""
        resp = _make_httpx_response({"error": "not found"}, status_code=404)
        with patch.object(httpx, "post", return_value=resp):
            result = reasoner.analyze(payment_event, classification, policy_allowed)

        assert result.success is False
        assert result.is_fallback is True


# ---------------------------------------------------------------------------
# Extra: _parse_nim_response edge cases
# ---------------------------------------------------------------------------


class TestParseEdgeCases:
    def test_missing_message_key(self, policy_allowed):
        """Response body without 'message' key → fallback."""
        raw = {"choices": [{}]}
        result = _parse_nim_response(raw, policy_allowed, _TEST_MODEL)
        assert result.success is False
        assert result.is_fallback is True

    def test_empty_recommendation_string(self, policy_allowed):
        """Empty recommendation string → fallback."""
        raw = {
            "choices": [{"message": {
                "content": json.dumps(
                    {"recommendation": "   ", "explanation": "X", "confidence": 0.5}
                )
            }}]
        }
        result = _parse_nim_response(raw, policy_allowed, _TEST_MODEL)
        assert result.success is False
        assert result.is_fallback is True

    def test_negative_confidence(self, policy_allowed):
        """Negative confidence → fallback."""
        raw = {
            "choices": [{"message": {
                "content": json.dumps(
                    {"recommendation": "Retry", "explanation": "X", "confidence": -0.1}
                )
            }}]
        }
        result = _parse_nim_response(raw, policy_allowed, _TEST_MODEL)
        assert result.success is False
        assert result.is_fallback is True


# ---------------------------------------------------------------------------
# Optional integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntegration:
    """Integration tests that require a running NIM server.

    Run with: pytest -m integration
    Skip with: pytest -m "not integration" (default)
    """

    @pytest.fixture(autouse=True)
    def _check_nim(self):
        """Skip if NIM is not reachable."""
        try:
            resp = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
            if resp.status_code != 200:
                pytest.skip("NIM server returned non-200")
        except Exception:
            pytest.skip("NIM server not available at localhost:11434")

    def test_real_nim_call(self, payment_event, classification, policy_allowed):
        """End-to-end call to a real NIM instance."""
        reasoner = RecoveryReasoner(timeout=60.0)
        result = reasoner.analyze(payment_event, classification, policy_allowed)

        # We don't assert on content (model output varies), but structure
        # must be valid.
        assert isinstance(result, ReasoningResult)
        assert result.model_id == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
        if result.success:
            assert result.is_fallback is False
            assert 0.0 <= result.confidence <= 1.0
            assert len(result.recommendation) > 0
            assert len(result.explanation) > 0
        else:
            # Model might not be available, but we should get a clean fallback
            assert result.is_fallback is True

    def test_real_nim_denied_policy(
        self, payment_event, classification, policy_denied
    ):
        """Real NIM call with denied policy → policy_action_allowed=False."""
        reasoner = RecoveryReasoner(timeout=60.0)
        result = reasoner.analyze(payment_event, classification, policy_denied)

        assert isinstance(result, ReasoningResult)
        # Critical: even with a real model, policy denial must be preserved
        assert result.policy_action_allowed is False


def test_no_api_key_skips_network_call(payment_event, classification, policy_allowed):
    """With no API key configured, analyze() must return a deterministic
    fallback without making any network call."""
    from unittest.mock import MagicMock

    reasoner = RecoveryReasoner(
        nim_api_key="",
        nim_base_url=_TEST_NIM_URL,
        nim_model=_TEST_MODEL,
        timeout=5.0,
    )
    mock_post = MagicMock()
    with patch.object(httpx, "post", mock_post):
        result = reasoner.analyze(payment_event, classification, policy_allowed)

    assert mock_post.call_count == 0
    assert result.is_fallback is True
    assert result.success is False
    # Never invents authorization; mirrors the policy decision verbatim.
    assert result.policy_action_allowed == policy_allowed.automatic_recovery_allowed
