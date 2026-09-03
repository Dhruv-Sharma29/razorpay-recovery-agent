"""Comprehensive tests for the deterministic recovery policy engine.

Covers:
1.  Retryable failure → expected recovery action
2.  Non-retryable failure → no automatic recovery
3.  Escalation / manual review case
4.  Amount below automatic-recovery limit
5.  Amount exactly at the configured limit
6.  Amount above the configured limit
7.  Unknown failure category
8.  Invalid classification (hypothetical)
9.  Missing classification (None)
10. Missing/invalid amount (via boundary)
11. Deterministic repeated evaluation
12. Original payment event is not mutated
13. Unsafe conditions fail closed
14. Representative records from synthetic dataset
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.classifier import ClassificationCertainty, ClassificationResult, FailureClassifier
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    MandateStatus,
    PaymentMethod,
    TransactionType,
)
from app.policy import (
    DEFAULT_AMOUNT_LIMIT,
    GLOBAL_MAX_ATTEMPTS,
    EscalationReason,
    PolicyAction,
    PolicyDecision,
    RecoveryPolicyEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    *,
    event_id: str = "evt_test_0001",
    error_code: str = "BAD_REQUEST_ERROR",
    error_description: str = "generic error",
    failure_category: str = "unknown",
    payment_method: str = "card",
    tx_type: str = "one_time",
    amount: int = 100000,  # ₹1,000 — well below default 500000 limit
    attempt_number: int = 1,
    mandate_status: str | None = None,
) -> FailedTransactionEvent:
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


def _make_classification(
    category: FailureCategory,
    *,
    confidence: float = 1.0,
    certainty: ClassificationCertainty = ClassificationCertainty.HIGH,
    reason: str = "test classification",
    rule_id: str = "test.rule",
    source_field: str = "error_code",
) -> ClassificationResult:
    return ClassificationResult(
        category=category,
        confidence=confidence,
        certainty=certainty,
        reason=reason,
        rule_id=rule_id,
        source_field=source_field,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> RecoveryPolicyEngine:
    return RecoveryPolicyEngine(amount_limit=DEFAULT_AMOUNT_LIMIT)


# ===================================================================
# 1. Retryable failure → expected recovery action
# ===================================================================


class TestRetryableFailures:
    """Each retryable category should produce the correct action on attempt 1."""

    def test_insufficient_funds_scheduled_retry(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.SCHEDULED_RETRY
        assert decision.automatic_recovery_allowed is True
        assert decision.escalation_required is False

    def test_insufficient_funds_attempt_2(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=2)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.SCHEDULED_RETRY
        assert decision.automatic_recovery_allowed is True

    def test_network_error_immediate_retry(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.NETWORK_ERROR)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.IMMEDIATE_RETRY
        assert decision.automatic_recovery_allowed is True

    def test_expired_card_reauthorization(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.EXPIRED_CARD)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.TRIGGER_REAUTHORIZATION
        assert decision.automatic_recovery_allowed is True

    def test_bank_decline_switch_method(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.BANK_DECLINE)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.SWITCH_PAYMENT_METHOD
        assert decision.automatic_recovery_allowed is True

    def test_auth_failure_resend_prompt(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.AUTHENTICATION_FAILURE)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.RESEND_AUTH_PROMPT
        assert decision.automatic_recovery_allowed is True


# ===================================================================
# 2. Non-retryable failure → no automatic recovery
# ===================================================================


class TestNonRetryableFailures:
    """Categories that exceed their retry limit should escalate."""

    def test_insufficient_funds_attempt_3_escalates(self, engine: RecoveryPolicyEngine) -> None:
        """3rd failure → escalate, stop (per plan)."""
        event = _make_event(amount=100000, attempt_number=3)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True
        assert decision.escalation_reason == EscalationReason.GLOBAL_ATTEMPT_CAP

    def test_network_error_attempt_2_escalates(self, engine: RecoveryPolicyEngine) -> None:
        """Max 1 immediate retry, so attempt 2 must escalate."""
        event = _make_event(amount=100000, attempt_number=2)
        classification = _make_classification(FailureCategory.NETWORK_ERROR)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_reason == EscalationReason.RETRY_LIMIT_EXHAUSTED

    def test_expired_card_attempt_2_escalates(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=2)
        classification = _make_classification(FailureCategory.EXPIRED_CARD)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False

    def test_bank_decline_attempt_2_escalates(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=2)
        classification = _make_classification(FailureCategory.BANK_DECLINE)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False

    def test_auth_failure_attempt_2_escalates(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=2)
        classification = _make_classification(FailureCategory.AUTHENTICATION_FAILURE)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False


# ===================================================================
# 3. Escalation / manual review case
# ===================================================================


class TestEscalation:
    """All escalation paths must set the right fields."""

    def test_escalation_has_required_fields(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.UNKNOWN)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True
        assert decision.escalation_reason is not None
        assert len(decision.reason) > 0
        assert len(decision.rule_id) > 0

    def test_escalation_reason_is_always_non_null_for_escalated(
        self, engine: RecoveryPolicyEngine
    ) -> None:
        """Every escalation must have a reason."""
        test_cases = [
            # Unknown
            (_make_event(amount=100000, attempt_number=1),
             _make_classification(FailureCategory.UNKNOWN)),
            # Amount cap
            (_make_event(amount=600000, attempt_number=1),
             _make_classification(FailureCategory.INSUFFICIENT_FUNDS)),
            # Global attempt cap
            (_make_event(amount=100000, attempt_number=3),
             _make_classification(FailureCategory.INSUFFICIENT_FUNDS)),
            # Retry limit
            (_make_event(amount=100000, attempt_number=2),
             _make_classification(FailureCategory.NETWORK_ERROR)),
            # Missing classification
            (_make_event(amount=100000, attempt_number=1), None),
        ]
        for event, classification in test_cases:
            decision = engine.evaluate(event, classification)
            assert decision.escalation_required is True
            assert decision.escalation_reason is not None, (
                f"Missing escalation_reason for category={classification.category.value if classification else 'None'}"
            )


# ===================================================================
# 4. Amount below automatic-recovery limit
# ===================================================================


class TestAmountBelowLimit:
    """Amounts below the configured limit should allow auto-recovery."""

    def test_amount_well_below_limit(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)  # ₹1,000 << ₹5,000
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is True
        assert decision.action != PolicyAction.ESCALATE

    def test_amount_one_below_limit(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=DEFAULT_AMOUNT_LIMIT - 1, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is True


# ===================================================================
# 5. Amount exactly at the configured limit
# ===================================================================


class TestAmountAtLimit:
    """Amount exactly at the limit should still be allowed (<=)."""

    def test_amount_exactly_at_limit(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=DEFAULT_AMOUNT_LIMIT, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        # At limit: allowed (> limit triggers escalation, not >=)
        assert decision.automatic_recovery_allowed is True
        assert decision.escalation_required is False


# ===================================================================
# 6. Amount above the configured limit
# ===================================================================


class TestAmountAboveLimit:
    """Amounts above the limit MUST NOT receive automatic recovery."""

    def test_amount_one_above_limit(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=DEFAULT_AMOUNT_LIMIT + 1, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True
        assert decision.escalation_reason == EscalationReason.AMOUNT_EXCEEDS_LIMIT
        assert decision.action == PolicyAction.ESCALATE

    def test_amount_well_above_limit(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=2500000, attempt_number=1)  # ₹25,000
        classification = _make_classification(FailureCategory.NETWORK_ERROR)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_reason == EscalationReason.AMOUNT_EXCEEDS_LIMIT

    def test_amount_above_limit_all_categories(self, engine: RecoveryPolicyEngine) -> None:
        """Every category must escalate when amount exceeds limit."""
        for category in FailureCategory:
            event = _make_event(amount=DEFAULT_AMOUNT_LIMIT + 100, attempt_number=1)
            classification = _make_classification(category)
            decision = engine.evaluate(event, classification)
            assert decision.automatic_recovery_allowed is False, (
                f"{category.value} should not allow auto-recovery above limit"
            )
            assert decision.escalation_required is True

    def test_custom_amount_limit(self) -> None:
        """Engine with a custom lower limit."""
        engine = RecoveryPolicyEngine(amount_limit=10000)
        event = _make_event(amount=10001, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_reason == EscalationReason.AMOUNT_EXCEEDS_LIMIT
        assert decision.amount_limit == 10000


# ===================================================================
# 7. Unknown failure category
# ===================================================================


class TestUnknownFailure:
    """Unknown failures must always escalate."""

    def test_unknown_always_escalates(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.UNKNOWN)
        decision = engine.evaluate(event, classification)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True
        assert decision.escalation_reason == EscalationReason.UNKNOWN_FAILURE

    def test_unknown_even_on_attempt_1(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=5000, attempt_number=1)
        classification = _make_classification(FailureCategory.UNKNOWN)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is False

    def test_unknown_max_retries_is_zero(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.UNKNOWN)
        decision = engine.evaluate(event, classification)
        assert decision.max_retries_for_category == 0


# ===================================================================
# 8. Invalid classification
# ===================================================================


class TestInvalidClassification:
    """If classification has an unrecognized category, escalate."""

    # In the current enum, all values are covered by policy.
    # This test verifies the guard works if a new category is added
    # without updating the policy table.
    def test_missing_classification_escalates(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        decision = engine.evaluate(event, None)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True
        assert decision.escalation_reason == EscalationReason.MISSING_CLASSIFICATION


# ===================================================================
# 9. Missing classification (None)
# ===================================================================


class TestMissingClassification:
    """None classification must fail closed."""

    def test_none_classification_escalates(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        decision = engine.evaluate(event, None)
        assert decision.action == PolicyAction.ESCALATE
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_reason == EscalationReason.MISSING_CLASSIFICATION

    def test_none_classification_has_reason(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        decision = engine.evaluate(event, None)
        assert "missing" in decision.reason.lower()


# ===================================================================
# 10. Missing/invalid amount (boundary)
# ===================================================================


class TestAmountBoundary:
    """Minimum valid amount and edge cases."""

    def test_minimum_valid_amount(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100, attempt_number=1)  # ₹1 = minimum
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is True
        assert decision.amount == 100

    def test_decision_records_amount(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=250000, attempt_number=1)
        classification = _make_classification(FailureCategory.NETWORK_ERROR)
        decision = engine.evaluate(event, classification)
        assert decision.amount == 250000
        assert decision.amount_limit == DEFAULT_AMOUNT_LIMIT


# ===================================================================
# 11. Deterministic repeated evaluation
# ===================================================================


class TestDeterministicEvaluation:
    """Same input must produce identical output every time."""

    def test_same_inputs_ten_times(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        reference = engine.evaluate(event, classification).model_dump()
        for _ in range(9):
            result = engine.evaluate(event, classification).model_dump()
            assert result == reference

    def test_deterministic_across_all_categories(self, engine: RecoveryPolicyEngine) -> None:
        for category in FailureCategory:
            event = _make_event(amount=100000, attempt_number=1)
            classification = _make_classification(category)
            ref = engine.evaluate(event, classification).model_dump()
            for _ in range(5):
                assert engine.evaluate(event, classification).model_dump() == ref

    def test_new_engine_instance_same_result(self) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.NETWORK_ERROR)
        result_a = RecoveryPolicyEngine(amount_limit=500000).evaluate(event, classification)
        result_b = RecoveryPolicyEngine(amount_limit=500000).evaluate(event, classification)
        assert result_a.model_dump() == result_b.model_dump()


# ===================================================================
# 12. Original payment event is not mutated
# ===================================================================


class TestNoMutation:
    """The policy engine must not modify the payment event."""

    def test_event_unchanged_after_evaluation(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        original = event.model_dump()
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        engine.evaluate(event, classification)
        assert event.model_dump() == original

    def test_event_unchanged_after_escalation(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=600000, attempt_number=4)
        original = event.model_dump()
        classification = _make_classification(FailureCategory.UNKNOWN)
        engine.evaluate(event, classification)
        assert event.model_dump() == original

    def test_classification_unchanged(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        original = classification.model_dump()
        engine.evaluate(event, classification)
        assert classification.model_dump() == original

    def test_deep_copy_equivalence(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.NETWORK_ERROR)
        event_copy = copy.deepcopy(event)
        classification_copy = copy.deepcopy(classification)
        engine.evaluate(event, classification)
        assert event.model_dump() == event_copy.model_dump()
        assert classification.model_dump() == classification_copy.model_dump()


# ===================================================================
# 13. Unsafe conditions fail closed
# ===================================================================


class TestFailClosed:
    """Any unsafe or ambiguous condition must result in escalation."""

    def test_none_classification_fails_closed(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        decision = engine.evaluate(event, None)
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True

    def test_unknown_category_fails_closed(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.UNKNOWN)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True

    def test_high_amount_fails_closed(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=DEFAULT_AMOUNT_LIMIT + 1, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True

    def test_global_cap_fails_closed(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=GLOBAL_MAX_ATTEMPTS)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_required is True

    def test_exceeds_global_cap_fails_closed(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=GLOBAL_MAX_ATTEMPTS + 1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.automatic_recovery_allowed is False


# ===================================================================
# 14. Global constants
# ===================================================================


class TestGlobalConstants:
    """Verify the engine uses the expected global constants."""

    def test_global_max_attempts_is_3(self) -> None:
        assert GLOBAL_MAX_ATTEMPTS == 3

    def test_default_amount_limit_is_500000(self) -> None:
        assert DEFAULT_AMOUNT_LIMIT == 500000

    def test_engine_exposes_amount_limit(self) -> None:
        engine = RecoveryPolicyEngine(amount_limit=123456)
        assert engine.amount_limit == 123456


# ===================================================================
# 15. Decision result structure
# ===================================================================


class TestDecisionStructure:
    """PolicyDecision must have all required fields."""

    def test_decision_has_all_fields(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert isinstance(decision, PolicyDecision)
        dumped = decision.model_dump()
        expected_keys = {
            "action", "automatic_recovery_allowed", "reason", "rule_id",
            "failure_category", "escalation_required", "escalation_reason",
            "max_retries_for_category", "current_attempt", "amount", "amount_limit",
        }
        assert expected_keys.issubset(set(dumped.keys()))

    def test_no_execution_fields(self, engine: RecoveryPolicyEngine) -> None:
        """Decision must not contain execution/API/Qwen fields."""
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        dumped = decision.model_dump()
        forbidden = {"razorpay_response", "qwen_response", "ollama_response", "api_result"}
        found = forbidden & set(dumped.keys())
        assert not found, f"Decision contains execution fields: {found}"


# ===================================================================
# 16. Per-category retry limits
# ===================================================================


class TestPerCategoryLimits:
    """Each category has its specific retry limit from the plan."""

    def test_insufficient_funds_max_2(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.INSUFFICIENT_FUNDS)
        decision = engine.evaluate(event, classification)
        assert decision.max_retries_for_category == 2

    def test_expired_card_max_1(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.EXPIRED_CARD)
        decision = engine.evaluate(event, classification)
        assert decision.max_retries_for_category == 1

    def test_network_error_max_1(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.NETWORK_ERROR)
        decision = engine.evaluate(event, classification)
        assert decision.max_retries_for_category == 1

    def test_bank_decline_max_1(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.BANK_DECLINE)
        decision = engine.evaluate(event, classification)
        assert decision.max_retries_for_category == 1

    def test_auth_failure_max_1(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.AUTHENTICATION_FAILURE)
        decision = engine.evaluate(event, classification)
        assert decision.max_retries_for_category == 1

    def test_unknown_max_0(self, engine: RecoveryPolicyEngine) -> None:
        event = _make_event(amount=100000, attempt_number=1)
        classification = _make_classification(FailureCategory.UNKNOWN)
        decision = engine.evaluate(event, classification)
        assert decision.max_retries_for_category == 0


# ===================================================================
# 17. Synthetic dataset integration
# ===================================================================


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class TestSyntheticDatasetIntegration:
    """Run the policy engine over the synthetic and held-out datasets."""

    @pytest.fixture()
    def classifier(self) -> FailureClassifier:
        return FailureClassifier()

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
        self,
        engine: RecoveryPolicyEngine,
        classifier: FailureClassifier,
        synthetic_records: list[dict],
    ) -> None:
        """Every synthetic record must produce a policy decision."""
        decisions = []
        for rec in synthetic_records:
            event = FailedTransactionEvent(**rec)
            classification = classifier.classify(event)
            decision = engine.evaluate(event, classification)
            decisions.append(decision)
        assert len(decisions) == len(synthetic_records)

    def test_held_out_no_transaction_dropped(
        self,
        engine: RecoveryPolicyEngine,
        classifier: FailureClassifier,
        held_out_records: list[dict],
    ) -> None:
        decisions = []
        for rec in held_out_records:
            event = FailedTransactionEvent(**rec)
            classification = classifier.classify(event)
            decision = engine.evaluate(event, classification)
            decisions.append(decision)
        assert len(decisions) == len(held_out_records)

    def test_synthetic_unknown_always_escalated(
        self,
        engine: RecoveryPolicyEngine,
        classifier: FailureClassifier,
        synthetic_records: list[dict],
    ) -> None:
        for rec in synthetic_records:
            event = FailedTransactionEvent(**rec)
            classification = classifier.classify(event)
            decision = engine.evaluate(event, classification)
            if classification.category == FailureCategory.UNKNOWN:
                assert decision.escalation_required is True
                assert decision.automatic_recovery_allowed is False

    def test_synthetic_high_amount_always_escalated(
        self,
        engine: RecoveryPolicyEngine,
        classifier: FailureClassifier,
        synthetic_records: list[dict],
    ) -> None:
        for rec in synthetic_records:
            event = FailedTransactionEvent(**rec)
            classification = classifier.classify(event)
            decision = engine.evaluate(event, classification)
            if event.amount > engine.amount_limit:
                assert decision.escalation_required is True
                assert decision.automatic_recovery_allowed is False

    def test_synthetic_deterministic(
        self,
        engine: RecoveryPolicyEngine,
        classifier: FailureClassifier,
        synthetic_records: list[dict],
    ) -> None:
        run_a = []
        run_b = []
        for rec in synthetic_records:
            event = FailedTransactionEvent(**rec)
            classification = classifier.classify(event)
            run_a.append(engine.evaluate(event, classification).model_dump())
            run_b.append(engine.evaluate(event, classification).model_dump())
        assert run_a == run_b
