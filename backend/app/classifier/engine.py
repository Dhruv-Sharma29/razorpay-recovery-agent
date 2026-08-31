"""Rules-first deterministic failure classifier.

Classification is purely local: same input always yields the same output.
No LLM, network, or random calls are used.

Rule precedence (highest first):
  1. Specific Razorpay-style error codes (exact, case-insensitive)
  2. Message-pattern matching (case-insensitive substrings; more specific
     phrases before generic ones such as "declined")
  3. Unrecognized / missing signals → FailureCategory.UNKNOWN

Generic codes such as BAD_REQUEST_ERROR and SERVER_ERROR never win over
message matching. GATEWAY_ERROR is treated as a specific network/gateway code.

The pre-labeled ``failure_category`` on a payment event is ignored so gold
labels cannot leak into the decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.classifier.result import ClassificationCertainty, ClassificationResult
from app.models.payment_event import FailedTransactionEvent, FailureCategory

# Task brief uses "other" for unrecognized failures. The existing TASK-002
# schema and synthetic dataset use FailureCategory.UNKNOWN ("unknown").
UNRECOGNIZED_CATEGORY = FailureCategory.UNKNOWN


@dataclass(frozen=True)
class _CodeRule:
    code: str
    category: FailureCategory
    rule_id: str
    reason: str


@dataclass(frozen=True)
class _MessageRule:
    phrases: tuple[str, ...]
    category: FailureCategory
    rule_id: str
    reason: str


# Specific error codes only. Generic BAD_REQUEST_ERROR / SERVER_ERROR are omitted
# so they fall through to message matching.
_SPECIFIC_CODE_RULES: tuple[_CodeRule, ...] = (
    _CodeRule(
        "INSUFFICIENT_FUNDS",
        FailureCategory.INSUFFICIENT_FUNDS,
        "code.insufficient_funds",
        "Error code INSUFFICIENT_FUNDS maps to insufficient_funds",
    ),
    _CodeRule(
        "CARD_EXPIRED",
        FailureCategory.EXPIRED_CARD,
        "code.expired_card",
        "Error code CARD_EXPIRED maps to expired_card",
    ),
    _CodeRule(
        "EXPIRED_CARD",
        FailureCategory.EXPIRED_CARD,
        "code.expired_card",
        "Error code EXPIRED_CARD maps to expired_card",
    ),
    _CodeRule(
        "MANDATE_EXPIRED",
        FailureCategory.EXPIRED_CARD,
        "code.mandate_expired",
        "Error code MANDATE_EXPIRED maps to expired_card",
    ),
    _CodeRule(
        "GATEWAY_ERROR",
        FailureCategory.NETWORK_ERROR,
        "code.gateway_error",
        "Error code GATEWAY_ERROR maps to network_error",
    ),
    _CodeRule(
        "GATEWAY_TIMEOUT",
        FailureCategory.NETWORK_ERROR,
        "code.gateway_timeout",
        "Error code GATEWAY_TIMEOUT maps to network_error",
    ),
    _CodeRule(
        "NETWORK_ERROR",
        FailureCategory.NETWORK_ERROR,
        "code.network_error",
        "Error code NETWORK_ERROR maps to network_error",
    ),
    _CodeRule(
        "CARD_DECLINED",
        FailureCategory.BANK_DECLINE,
        "code.card_declined",
        "Error code CARD_DECLINED maps to bank_decline",
    ),
    _CodeRule(
        "BANK_DECLINED",
        FailureCategory.BANK_DECLINE,
        "code.bank_declined",
        "Error code BANK_DECLINED maps to bank_decline",
    ),
    _CodeRule(
        "ISSUER_DECLINED",
        FailureCategory.BANK_DECLINE,
        "code.issuer_declined",
        "Error code ISSUER_DECLINED maps to bank_decline",
    ),
    _CodeRule(
        "AUTHENTICATION_ERROR",
        FailureCategory.AUTHENTICATION_FAILURE,
        "code.authentication_error",
        "Error code AUTHENTICATION_ERROR maps to authentication_failure",
    ),
    _CodeRule(
        "AUTHENTICATION_FAILED",
        FailureCategory.AUTHENTICATION_FAILURE,
        "code.authentication_failed",
        "Error code AUTHENTICATION_FAILED maps to authentication_failure",
    ),
)

# More specific phrases first. Generic "declined" is last among message rules
# so "declined: insufficient balance" and "declined: card expiry" do not
# become bank_decline.
_MESSAGE_RULES: tuple[_MessageRule, ...] = (
    _MessageRule(
        ("insufficient fund", "insufficient balance", "insufficient account"),
        FailureCategory.INSUFFICIENT_FUNDS,
        "message.insufficient_funds",
        "Error description matches an insufficient-funds phrase",
    ),
    _MessageRule(
        (
            "card has expired",
            "card expired",
            "card expiry",
            "expiry date has passed",
        ),
        FailureCategory.EXPIRED_CARD,
        "message.expired_card",
        "Error description matches an expired-card phrase",
    ),
    _MessageRule(
        (
            "3d secure",
            "3ds",
            "otp",
            "authentication failed",
            "could not be authenticated",
        ),
        FailureCategory.AUTHENTICATION_FAILURE,
        "message.authentication_failure",
        "Error description matches an authentication / OTP / 3DS phrase",
    ),
    _MessageRule(
        (
            "gateway timeout",
            "gateway connection timed out",
            "network error",
            "did not respond in time",
        ),
        FailureCategory.NETWORK_ERROR,
        "message.network_error",
        "Error description matches a gateway / network-timeout phrase",
    ),
    _MessageRule(
        (
            "issuing bank",
            "card issuer",
            "issuer declined",
            "declined by the issuing",
            "not permitted by the card issuer",
        ),
        FailureCategory.BANK_DECLINE,
        "message.bank_decline",
        "Error description matches an issuer / bank-decline phrase",
    ),
    _MessageRule(
        ("declined",),
        FailureCategory.BANK_DECLINE,
        "message.bank_decline_generic",
        "Error description matches a generic decline phrase",
    ),
)

_GENERIC_ERROR_CODES = frozenset({"BAD_REQUEST_ERROR", "SERVER_ERROR"})


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_code(value: Any) -> str:
    return _normalize_text(value).upper()


def _normalize_message(value: Any) -> str:
    return _normalize_text(value).lower()


def _extract_fields(event: Any) -> dict[str, Any]:
    """Read classification inputs without mutating *event*."""
    if event is None:
        return {"error_code": "", "error_description": ""}

    if isinstance(event, FailedTransactionEvent):
        return {
            "error_code": event.error_code,
            "error_description": event.error_description,
        }

    if isinstance(event, Mapping):
        return {
            "error_code": event.get("error_code"),
            "error_description": event.get("error_description"),
        }

    return {
        "error_code": getattr(event, "error_code", None),
        "error_description": getattr(event, "error_description", None),
    }


class FailureClassifier:
    """Deterministic, explainable payment-failure classifier."""

    def classify(self, event: Any) -> ClassificationResult:
        """Classify a failed payment event.

        Accepts a ``FailedTransactionEvent``, a mapping, or a loosely
        structured object. Missing or null error fields are treated as
        unrecognized rather than raising.
        """
        fields = _extract_fields(event)
        error_code = _normalize_code(fields.get("error_code"))
        error_description = _normalize_message(fields.get("error_description"))

        code_result = self._match_specific_code(error_code)
        if code_result is not None:
            return code_result

        message_result = self._match_message(error_description)
        if message_result is not None:
            return message_result

        if error_code in _GENERIC_ERROR_CODES:
            return ClassificationResult(
                category=UNRECOGNIZED_CATEGORY,
                confidence=0.4,
                certainty=ClassificationCertainty.LOW,
                reason=(
                    f"Error code {error_code} is generic and the description "
                    "did not match a known failure phrase"
                ),
                rule_id="fallback.generic_code",
                source_field="error_code",
            )

        if not error_code and not error_description:
            return ClassificationResult(
                category=UNRECOGNIZED_CATEGORY,
                confidence=0.0,
                certainty=ClassificationCertainty.LOW,
                reason="No error code or error description was provided",
                rule_id="fallback.missing_failure_info",
                source_field="none",
            )

        return ClassificationResult(
            category=UNRECOGNIZED_CATEGORY,
            confidence=0.2,
            certainty=ClassificationCertainty.LOW,
            reason="No specific error code or message pattern matched",
            rule_id="fallback.unrecognized",
            source_field="none",
        )

    def _match_specific_code(self, error_code: str) -> ClassificationResult | None:
        if not error_code:
            return None
        for rule in _SPECIFIC_CODE_RULES:
            if error_code == rule.code:
                return ClassificationResult(
                    category=rule.category,
                    confidence=1.0,
                    certainty=ClassificationCertainty.HIGH,
                    reason=rule.reason,
                    rule_id=rule.rule_id,
                    source_field="error_code",
                )
        return None

    def _match_message(self, error_description: str) -> ClassificationResult | None:
        if not error_description:
            return None
        for rule in _MESSAGE_RULES:
            for phrase in rule.phrases:
                if phrase in error_description:
                    return ClassificationResult(
                        category=rule.category,
                        confidence=0.85,
                        certainty=ClassificationCertainty.MEDIUM,
                        reason=f"{rule.reason}: matched '{phrase}'",
                        rule_id=rule.rule_id,
                        source_field="error_description",
                    )
        return None
