"""Comprehensive tests for the deterministic failure classifier.

Covers:
- Every supported failure category with representative examples
- Specific error-code matching
- Message/description pattern matching
- Unknown / ambiguous inputs
- Malformed / missing fields
- Deterministic repeated classification
- Full synthetic and held-out dataset classification
- No transaction silently dropped
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.classifier import (
    ClassificationCertainty,
    ClassificationResult,
    FailureClassifier,
    UNRECOGNIZED_CATEGORY,
)
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    MandateStatus,
    PaymentMethod,
    TransactionType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def classifier() -> FailureClassifier:
    return FailureClassifier()


def _make_event(
    *,
    event_id: str = "evt_test_0001",
    error_code: str = "BAD_REQUEST_ERROR",
    error_description: str = "generic error",
    failure_category: str = "unknown",
    payment_method: str = "card",
    tx_type: str = "one_time",
    amount: int = 100000,
    attempt_number: int = 1,
    mandate_status: str | None = None,
) -> FailedTransactionEvent:
    """Build a minimal FailedTransactionEvent for testing."""
    return FailedTransactionEvent(
        event_id=event_id,
        razorpay_payment_id="pay_test_999999",
        merchant_id="merch_synth_001",
        customer_id="cust_synth_0001",
        type=TransactionType(tx_type),
        amount=amount,
        currency="INR",
        payment_method=PaymentMethod(payment_method),
        error_code=error_code,
        error_description=error_description,
        failure_category=FailureCategory(failure_category),
        attempt_number=attempt_number,
        mandate_status=MandateStatus(mandate_status) if mandate_status else None,
        timestamp="2026-08-28T12:00:00Z",
    )


# ===================================================================
# 1. Result model structure
# ===================================================================


class TestClassificationResult:
    """ClassificationResult must have all required fields."""

    def test_result_has_all_fields(self, classifier: FailureClassifier) -> None:
        event = _make_event(error_code="GATEWAY_ERROR")
        result = classifier.classify(event)
        assert isinstance(result, ClassificationResult)
        assert hasattr(result, "category")
        assert hasattr(result, "confidence")
        assert hasattr(result, "certainty")
        assert hasattr(result, "reason")
        assert hasattr(result, "rule_id")
        assert hasattr(result, "source_field")

    def test_confidence_range(self, classifier: FailureClassifier) -> None:
        event = _make_event(error_code="GATEWAY_ERROR")
        result = classifier.classify(event)
        assert 0.0 <= result.confidence <= 1.0

    def test_category_is_failure_category_enum(
        self, classifier: FailureClassifier
    ) -> None:
        event = _make_event(error_code="GATEWAY_ERROR")
        result = classifier.classify(event)
        assert isinstance(result.category, FailureCategory)

    def test_reason_is_non_empty_string(self, classifier: FailureClassifier) -> None:
        event = _make_event(error_code="GATEWAY_ERROR")
        result = classifier.classify(event)
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


# ===================================================================
# 2. Specific error-code classification
# ===================================================================


class TestErrorCodeClassification:
    """Specific Razorpay error codes must map to the correct category."""

    @pytest.mark.parametrize(
        "error_code,expected_category",
        [
            ("INSUFFICIENT_FUNDS", FailureCategory.INSUFFICIENT_FUNDS),
            ("CARD_EXPIRED", FailureCategory.EXPIRED_CARD),
            ("EXPIRED_CARD", FailureCategory.EXPIRED_CARD),
            ("MANDATE_EXPIRED", FailureCategory.EXPIRED_CARD),
            ("GATEWAY_ERROR", FailureCategory.NETWORK_ERROR),
            ("GATEWAY_TIMEOUT", FailureCategory.NETWORK_ERROR),
            ("NETWORK_ERROR", FailureCategory.NETWORK_ERROR),
            ("CARD_DECLINED", FailureCategory.BANK_DECLINE),
            ("BANK_DECLINED", FailureCategory.BANK_DECLINE),
            ("ISSUER_DECLINED", FailureCategory.BANK_DECLINE),
            ("AUTHENTICATION_ERROR", FailureCategory.AUTHENTICATION_FAILURE),
            ("AUTHENTICATION_FAILED", FailureCategory.AUTHENTICATION_FAILURE),
        ],
    )
    def test_specific_code_maps_correctly(
        self,
        classifier: FailureClassifier,
        error_code: str,
        expected_category: FailureCategory,
    ) -> None:
        event = _make_event(error_code=error_code)
        result = classifier.classify(event)
        assert result.category == expected_category
        assert result.source_field == "error_code"
        assert result.confidence == 1.0
        assert result.certainty == ClassificationCertainty.HIGH

    def test_specific_code_case_insensitive(
        self, classifier: FailureClassifier
    ) -> None:
        """Error codes are normalized to uppercase."""
        event = _make_event(error_code="gateway_error")
        result = classifier.classify(event)
        assert result.category == FailureCategory.NETWORK_ERROR


# ===================================================================
# 3. Message-pattern classification
# ===================================================================


class TestMessagePatternClassification:
    """Error descriptions must match the expected failure category."""

    @pytest.mark.parametrize(
        "error_description,expected_category",
        [
            # Insufficient funds
            (
                "Your payment could not be completed due to insufficient account balance",
                FailureCategory.INSUFFICIENT_FUNDS,
            ),
            (
                "Insufficient funds in the account to complete this transaction",
                FailureCategory.INSUFFICIENT_FUNDS,
            ),
            (
                "Transaction declined: insufficient balance",
                FailureCategory.INSUFFICIENT_FUNDS,
            ),
            # Expired card
            (
                "The card has expired. Please use a different card",
                FailureCategory.EXPIRED_CARD,
            ),
            (
                "Card expired — unable to process payment",
                FailureCategory.EXPIRED_CARD,
            ),
            (
                "Transaction declined: card expiry date has passed",
                FailureCategory.EXPIRED_CARD,
            ),
            # Network error
            (
                "Payment processing failed due to a gateway timeout",
                FailureCategory.NETWORK_ERROR,
            ),
            (
                "Network error: bank gateway did not respond in time",
                FailureCategory.NETWORK_ERROR,
            ),
            (
                "Gateway connection timed out while processing payment",
                FailureCategory.NETWORK_ERROR,
            ),
            # Bank decline
            (
                "The card issuing bank declined the transaction",
                FailureCategory.BANK_DECLINE,
            ),
            (
                "Payment declined by the issuing bank",
                FailureCategory.BANK_DECLINE,
            ),
            (
                "Transaction not permitted by the card issuer",
                FailureCategory.BANK_DECLINE,
            ),
            # Authentication failure
            (
                "3D Secure authentication failed or was not completed",
                FailureCategory.AUTHENTICATION_FAILURE,
            ),
            (
                "OTP verification failed — payment could not be authenticated",
                FailureCategory.AUTHENTICATION_FAILURE,
            ),
            (
                "Authentication failed: customer did not complete 3DS challenge",
                FailureCategory.AUTHENTICATION_FAILURE,
            ),
        ],
    )
    def test_message_maps_correctly(
        self,
        classifier: FailureClassifier,
        error_description: str,
        expected_category: FailureCategory,
    ) -> None:
        event = _make_event(
            error_code="BAD_REQUEST_ERROR",
            error_description=error_description,
        )
        result = classifier.classify(event)
        assert result.category == expected_category
        assert result.source_field == "error_description"
        assert result.confidence == 0.85
        assert result.certainty == ClassificationCertainty.MEDIUM

    def test_message_is_case_insensitive(
        self, classifier: FailureClassifier
    ) -> None:
        event = _make_event(
            error_code="BAD_REQUEST_ERROR",
            error_description="INSUFFICIENT FUNDS IN THE ACCOUNT",
        )
        result = classifier.classify(event)
        assert result.category == FailureCategory.INSUFFICIENT_FUNDS


# ===================================================================
# 4. Category-specific tests (every category)
# ===================================================================


class TestEveryCategory:
    """Ensure every planned category is reachable."""

    def test_insufficient_funds(self, classifier: FailureClassifier) -> None:
        result = classifier.classify(
            _make_event(error_code="INSUFFICIENT_FUNDS")
        )
        assert result.category == FailureCategory.INSUFFICIENT_FUNDS

    def test_expired_card(self, classifier: FailureClassifier) -> None:
        result = classifier.classify(
            _make_event(error_code="CARD_EXPIRED")
        )
        assert result.category == FailureCategory.EXPIRED_CARD

    def test_network_error(self, classifier: FailureClassifier) -> None:
        result = classifier.classify(
            _make_event(error_code="GATEWAY_ERROR")
        )
        assert result.category == FailureCategory.NETWORK_ERROR

    def test_bank_decline(self, classifier: FailureClassifier) -> None:
        result = classifier.classify(
            _make_event(error_code="CARD_DECLINED")
        )
        assert result.category == FailureCategory.BANK_DECLINE

    def test_authentication_failure(self, classifier: FailureClassifier) -> None:
        result = classifier.classify(
            _make_event(error_code="AUTHENTICATION_ERROR")
        )
        assert result.category == FailureCategory.AUTHENTICATION_FAILURE

    def test_unknown(self, classifier: FailureClassifier) -> None:
        result = classifier.classify(
            _make_event(
                error_code="SERVER_ERROR",
                error_description="An unexpected error occurred",
            )
        )
        assert result.category == FailureCategory.UNKNOWN


# ===================================================================
# 5. Unknown / ambiguous inputs
# ===================================================================


class TestUnknownAndAmbiguous:
    """Unknown or ambiguous inputs must classify as UNKNOWN safely."""

    def test_server_error_no_matching_description(
        self, classifier: FailureClassifier
    ) -> None:
        event = _make_event(
            error_code="SERVER_ERROR",
            error_description="Something went wrong internally",
        )
        result = classifier.classify(event)
        assert result.category == FailureCategory.UNKNOWN
        assert result.certainty == ClassificationCertainty.LOW

    def test_generic_bad_request_unknown_message(
        self, classifier: FailureClassifier
    ) -> None:
        event = _make_event(
            error_code="BAD_REQUEST_ERROR",
            error_description="Some completely unexpected error text",
        )
        result = classifier.classify(event)
        assert result.category == FailureCategory.UNKNOWN

    def test_completely_novel_error_code(
        self, classifier: FailureClassifier
    ) -> None:
        event = _make_event(
            error_code="BRAND_NEW_ERROR_CODE_2027",
            error_description="Never seen before",
        )
        result = classifier.classify(event)
        assert result.category == FailureCategory.UNKNOWN

    def test_unrecognized_category_is_unknown(self) -> None:
        assert UNRECOGNIZED_CATEGORY == FailureCategory.UNKNOWN


# ===================================================================
# 6. Malformed / missing fields
# ===================================================================


class TestMalformedInputs:
    """Classifier must handle malformed or missing data gracefully."""

    def test_dict_input_accepted(self, classifier: FailureClassifier) -> None:
        result = classifier.classify({
            "error_code": "GATEWAY_ERROR",
            "error_description": "timeout",
        })
        assert result.category == FailureCategory.NETWORK_ERROR

    def test_dict_missing_error_code(self, classifier: FailureClassifier) -> None:
        result = classifier.classify({
            "error_description": "insufficient funds",
        })
        assert result.category == FailureCategory.INSUFFICIENT_FUNDS

    def test_dict_missing_error_description(
        self, classifier: FailureClassifier
    ) -> None:
        result = classifier.classify({
            "error_code": "CARD_DECLINED",
        })
        assert result.category == FailureCategory.BANK_DECLINE

    def test_dict_missing_both_fields(self, classifier: FailureClassifier) -> None:
        result = classifier.classify({})
        assert result.category == FailureCategory.UNKNOWN
        assert result.confidence == 0.0

    def test_none_input(self, classifier: FailureClassifier) -> None:
        result = classifier.classify(None)
        assert result.category == FailureCategory.UNKNOWN
        assert result.confidence == 0.0

    def test_empty_strings(self, classifier: FailureClassifier) -> None:
        result = classifier.classify({
            "error_code": "",
            "error_description": "",
        })
        assert result.category == FailureCategory.UNKNOWN

    def test_none_field_values(self, classifier: FailureClassifier) -> None:
        result = classifier.classify({
            "error_code": None,
            "error_description": None,
        })
        assert result.category == FailureCategory.UNKNOWN


# ===================================================================
# 7. Deterministic repeated classification
# ===================================================================


class TestDeterministicClassification:
    """Same input must always produce the same output."""

    def test_same_event_same_result_ten_times(
        self, classifier: FailureClassifier
    ) -> None:
        event = _make_event(
            error_code="BAD_REQUEST_ERROR",
            error_description="Insufficient funds in the account to complete this transaction",
            failure_category="insufficient_funds",
        )
        reference = classifier.classify(event)
        for _ in range(9):
            result = classifier.classify(event)
            assert result.model_dump() == reference.model_dump()

    def test_deterministic_across_all_categories(
        self, classifier: FailureClassifier
    ) -> None:
        events = [
            _make_event(error_code="INSUFFICIENT_FUNDS"),
            _make_event(error_code="CARD_EXPIRED"),
            _make_event(error_code="GATEWAY_ERROR"),
            _make_event(error_code="CARD_DECLINED"),
            _make_event(error_code="AUTHENTICATION_ERROR"),
            _make_event(
                error_code="SERVER_ERROR",
                error_description="Unknown failure",
            ),
        ]
        for event in events:
            ref = classifier.classify(event)
            for _ in range(5):
                assert classifier.classify(event).model_dump() == ref.model_dump()

    def test_new_classifier_instance_same_result(self) -> None:
        event = _make_event(error_code="GATEWAY_ERROR")
        result_a = FailureClassifier().classify(event)
        result_b = FailureClassifier().classify(event)
        assert result_a.model_dump() == result_b.model_dump()


# ===================================================================
# 8. Rule precedence
# ===================================================================


class TestRulePrecedence:
    """Specific codes take precedence over generic ones + message matching."""

    def test_specific_code_beats_message(
        self, classifier: FailureClassifier
    ) -> None:
        """GATEWAY_ERROR code should win even if description says 'declined'."""
        event = _make_event(
            error_code="GATEWAY_ERROR",
            error_description="Payment declined by the issuing bank",
        )
        result = classifier.classify(event)
        assert result.category == FailureCategory.NETWORK_ERROR
        assert result.source_field == "error_code"

    def test_generic_code_falls_through_to_message(
        self, classifier: FailureClassifier
    ) -> None:
        """BAD_REQUEST_ERROR is generic, so message matching should fire."""
        event = _make_event(
            error_code="BAD_REQUEST_ERROR",
            error_description="Card expired — unable to process payment",
        )
        result = classifier.classify(event)
        assert result.category == FailureCategory.EXPIRED_CARD
        assert result.source_field == "error_description"

    def test_insufficient_balance_not_misclassified_as_decline(
        self, classifier: FailureClassifier
    ) -> None:
        """'Transaction declined: insufficient balance' should be insufficient_funds, not bank_decline."""
        event = _make_event(
            error_code="BAD_REQUEST_ERROR",
            error_description="Transaction declined: insufficient balance",
        )
        result = classifier.classify(event)
        assert result.category == FailureCategory.INSUFFICIENT_FUNDS

    def test_card_expiry_not_misclassified_as_decline(
        self, classifier: FailureClassifier
    ) -> None:
        """'Transaction declined: card expiry date has passed' should be expired_card."""
        event = _make_event(
            error_code="BAD_REQUEST_ERROR",
            error_description="Transaction declined: card expiry date has passed",
        )
        result = classifier.classify(event)
        assert result.category == FailureCategory.EXPIRED_CARD


# ===================================================================
# 9. Classification does NOT make recovery decisions
# ===================================================================


class TestNoRecoveryDecision:
    """Classifier output must not contain policy/recovery fields."""

    def test_result_has_no_action_field(
        self, classifier: FailureClassifier
    ) -> None:
        event = _make_event(error_code="GATEWAY_ERROR")
        result = classifier.classify(event)
        dumped = result.model_dump()
        forbidden_keys = {"action", "retry", "escalate", "scheduled_for", "action_taken"}
        found = forbidden_keys & set(dumped.keys())
        assert not found, f"Classifier result contains policy fields: {found}"


# ===================================================================
# 10. Full dataset classification
# ===================================================================


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class TestFullDatasetClassification:
    """Run the classifier over the complete synthetic and held-out datasets."""

    @pytest.fixture()
    def synthetic_records(self) -> list[dict]:
        path = _DATA_DIR / "synthetic" / "failed_transactions.json"
        if not path.exists():
            pytest.skip(f"Synthetic dataset not found at {path}")
        return json.loads(path.read_text())

    @pytest.fixture()
    def held_out_records(self) -> list[dict]:
        path = _DATA_DIR / "held_out" / "failed_transactions.json"
        if not path.exists():
            pytest.skip(f"Held-out dataset not found at {path}")
        return json.loads(path.read_text())

    def test_synthetic_no_transaction_dropped(
        self, classifier: FailureClassifier, synthetic_records: list[dict]
    ) -> None:
        """Every record in the synthetic dataset must produce a result."""
        results = [classifier.classify(FailedTransactionEvent(**r)) for r in synthetic_records]
        assert len(results) == len(synthetic_records)

    def test_held_out_no_transaction_dropped(
        self, classifier: FailureClassifier, held_out_records: list[dict]
    ) -> None:
        """Every record in the held-out dataset must produce a result."""
        results = [classifier.classify(FailedTransactionEvent(**r)) for r in held_out_records]
        assert len(results) == len(held_out_records)

    def test_synthetic_matches_ground_truth(
        self, classifier: FailureClassifier, synthetic_records: list[dict]
    ) -> None:
        """Classifier should match the ground-truth category for every synthetic record."""
        for rec in synthetic_records:
            event = FailedTransactionEvent(**rec)
            result = classifier.classify(event)
            assert result.category.value == rec["failure_category"], (
                f"{rec['event_id']}: expected {rec['failure_category']}, "
                f"got {result.category.value} (rule={result.rule_id})"
            )

    def test_held_out_matches_ground_truth(
        self, classifier: FailureClassifier, held_out_records: list[dict]
    ) -> None:
        """Classifier should match the ground-truth category for every held-out record."""
        for rec in held_out_records:
            event = FailedTransactionEvent(**rec)
            result = classifier.classify(event)
            assert result.category.value == rec["failure_category"], (
                f"{rec['event_id']}: expected {rec['failure_category']}, "
                f"got {result.category.value} (rule={result.rule_id})"
            )

    def test_synthetic_all_categories_represented(
        self, classifier: FailureClassifier, synthetic_records: list[dict]
    ) -> None:
        results = [classifier.classify(FailedTransactionEvent(**r)) for r in synthetic_records]
        categories = {r.category.value for r in results}
        expected = {
            "insufficient_funds",
            "expired_card",
            "network_error",
            "bank_decline",
            "authentication_failure",
            "unknown",
        }
        assert categories == expected

    def test_synthetic_deterministic_classification(
        self, classifier: FailureClassifier, synthetic_records: list[dict]
    ) -> None:
        """Running classification twice on the synthetic set must give identical results."""
        run_a = [
            classifier.classify(FailedTransactionEvent(**r)).model_dump()
            for r in synthetic_records
        ]
        run_b = [
            classifier.classify(FailedTransactionEvent(**r)).model_dump()
            for r in synthetic_records
        ]
        assert run_a == run_b

    def test_held_out_deterministic_classification(
        self, classifier: FailureClassifier, held_out_records: list[dict]
    ) -> None:
        """Running classification twice on the held-out set must give identical results."""
        run_a = [
            classifier.classify(FailedTransactionEvent(**r)).model_dump()
            for r in held_out_records
        ]
        run_b = [
            classifier.classify(FailedTransactionEvent(**r)).model_dump()
            for r in held_out_records
        ]
        assert run_a == run_b
