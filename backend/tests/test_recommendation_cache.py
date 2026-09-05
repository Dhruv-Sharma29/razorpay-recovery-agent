"""Advisory recommendation cache.

Exists for two reasons, and the second matters more than the speed: both A/B
arms must reason from the *identical* advice, or the comparison measures the
model's non-determinism as well as the variable under test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.classifier.result import ClassificationCertainty, ClassificationResult
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)
from app.recommendation.cache import RecommendationCache
from app.recommendation.result import RecoveryRecommendation


def _event(**over) -> FailedTransactionEvent:
    base = dict(
        event_id="evt_1",
        razorpay_payment_id="pay_1",
        merchant_id="m1",
        customer_id="c1",
        type=TransactionType.ONE_TIME,
        amount=149900,
        currency="INR",
        payment_method=PaymentMethod.CARD,
        error_code="BANK_DECLINED",
        error_description="Issuer declined",
        failure_category=FailureCategory.UNKNOWN,
        attempt_number=1,
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    base.update(over)
    return FailedTransactionEvent(**base)


def _classification() -> ClassificationResult:
    return ClassificationResult(
        category=FailureCategory.BANK_DECLINE,
        confidence=0.95,
        certainty=ClassificationCertainty.HIGH,
        reason="test",
        rule_id="code.bank_decline",
        source_field="error_code",
    )


def _recommendation(**over) -> RecoveryRecommendation:
    base = dict(
        success=True,
        revenue_at_risk=True,
        risk_score=0.8,
        suggested_cause=FailureCategory.BANK_DECLINE,
        suggested_action=None,
        confidence=0.9,
        model_id="test-model",
        is_fallback=False,
    )
    base.update(over)
    return RecoveryRecommendation(**base)


@pytest.fixture()
def cache() -> RecommendationCache:
    return RecommendationCache(maxsize=3)


def _key(cache: RecommendationCache, **over):
    args = dict(
        event=_event(),
        classification=_classification(),
        available_actions=["switch_payment_method", "scheduled_retry"],
        observed_outcomes=[],
        cooldown_window={},
    )
    args.update(over)
    return cache.build_key(**args)


class TestKeyShape:
    def test_the_same_question_gets_the_same_key(self, cache) -> None:
        assert _key(cache) == _key(cache)

    def test_transient_identifiers_do_not_change_the_key(self, cache) -> None:
        """A new payment id per run must not defeat the cache."""
        other = _event(event_id="evt_2", razorpay_payment_id="pay_2")
        assert _key(cache) == _key(cache, event=other)

    def test_pii_and_amount_are_excluded(self, cache) -> None:
        """Advice depends on the failure's shape; the cap is policy's job."""
        other = _event(customer_id="someone_else", amount=999999)
        assert _key(cache) == _key(cache, event=other)

    def test_a_later_attempt_is_a_different_question(self, cache) -> None:
        assert _key(cache) != _key(cache, event=_event(attempt_number=3))

    def test_a_different_action_menu_changes_the_key(self, cache) -> None:
        assert _key(cache) != _key(cache, available_actions=["scheduled_retry"])

    def test_new_measured_outcomes_change_the_key(self, cache) -> None:
        """Fresh evidence must produce fresh advice, not a stale replay."""
        outcomes = [{"category": "bank_decline", "action": "x", "success_rate": 0.9}]
        assert _key(cache) != _key(cache, observed_outcomes=outcomes)


class TestStorage:
    def test_a_miss_returns_none_and_counts(self, cache) -> None:
        assert cache.get("absent") is None
        assert cache.stats()["misses"] == 1

    def test_a_stored_recommendation_comes_back(self, cache) -> None:
        cache.put("k", _recommendation())
        got = cache.get("k")
        assert got is not None
        assert got.confidence == 0.9
        assert cache.stats()["hits"] == 1

    def test_a_fallback_is_never_cached(self, cache) -> None:
        """A fallback records why the model was down at one moment. Replaying
        it would report an outage that has since passed."""
        cache.put("k", _recommendation(is_fallback=True))
        assert cache.get("k") is None

    def test_an_unsuccessful_result_is_never_cached(self, cache) -> None:
        cache.put("k", _recommendation(success=False))
        assert cache.get("k") is None

    def test_mutating_a_result_cannot_poison_the_cache(self, cache) -> None:
        cache.put("k", _recommendation())
        first = cache.get("k")
        first.confidence = 0.1
        assert cache.get("k").confidence == 0.9

    def test_it_evicts_the_least_recently_used(self, cache) -> None:
        for name in ("a", "b", "c"):
            cache.put(name, _recommendation())
        cache.get("a")           # 'a' is now the most recent
        cache.put("d", _recommendation())
        assert cache.get("b") is None, "'b' was least recently used"
        assert cache.get("a") is not None

    def test_clear_resets_counters(self, cache) -> None:
        cache.put("k", _recommendation())
        cache.get("k")
        cache.clear()
        assert cache.stats() == {"hits": 0, "misses": 0, "size": 0}
