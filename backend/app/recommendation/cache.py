"""Recommendation cache.

The advisor is asked the same question far more often than it looks. A batch
of 100 events contains perhaps six distinct decision shapes — a cause, the
actions policy permits for it, and what those actions have historically
recovered — and the A/B asks every one of them twice, once per arm.

At roughly thirteen seconds a call that is the difference between a
measurement you can run on camera and one you start and walk away from.

Caching also makes the A/B *more* correct, not just faster. Both arms now
reason from the identical advice and differ only in whether policy lets the
advisor act on it, which is the single variable the comparison exists to
isolate. Two independent calls to a non-deterministic model would reintroduce
exactly the luck-based variance the capture model was rewritten to remove.

The key deliberately excludes PII, transient identifiers, and the amount: the
advice depends on the shape of the failure, and the amount cap is policy's
decision to make, never the advisor's.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from threading import Lock
from typing import Any, Sequence

from app.classifier.result import ClassificationResult
from app.models.payment_event import FailedTransactionEvent
from app.recommendation.result import RecoveryRecommendation

logger = logging.getLogger(__name__)


class RecommendationCache:
    """Thread-safe LRU cache for advisory recommendations.

    A batch runs on a thread pool, so every access is locked.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._cache: OrderedDict[str, RecoveryRecommendation] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def build_key(
        event: FailedTransactionEvent,
        classification: ClassificationResult | None,
        available_actions: Sequence[str] | None,
        observed_outcomes: Sequence[dict] | None,
        cooldown_window: dict | None,
    ) -> str:
        """Everything that changes the answer, and nothing that does not.

        Attempt number is included because a third attempt is a different
        question from a first. Customer id, payment id and amount are not:
        they do not shape the advice, and two of them are PII.
        """
        shape: dict[str, Any] = {
            "category": (
                classification.category.value if classification is not None else None
            ),
            "rule": classification.rule_id if classification is not None else None,
            "attempt": event.attempt_number,
            "method": event.payment_method.value,
            "type": event.type.value,
            "mandate": (
                event.mandate_status.value
                if event.mandate_status is not None
                else None
            ),
            "actions": list(available_actions or []),
            "outcomes": list(observed_outcomes or []),
            "window": dict(cooldown_window or {}),
        }
        raw = json.dumps(shape, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> RecoveryRecommendation | None:
        """Return a cached recommendation, or None."""
        with self._lock:
            hit = self._cache.get(key)
            if hit is None:
                self.misses += 1
                return None
            self._cache.move_to_end(key)
            self.hits += 1
            # A copy, so a caller mutating its result cannot poison the cache.
            return hit.model_copy(deep=True)

    def put(self, key: str, recommendation: RecoveryRecommendation) -> None:
        """Store a recommendation.

        Fallbacks are never cached: they record *why* the model was
        unavailable at a moment in time, and replaying that later would report
        an outage that has since passed — and would keep the advisor silent
        long after it recovered.
        """
        if not recommendation.success or recommendation.is_fallback:
            return
        with self._lock:
            self._cache[key] = recommendation.model_copy(deep=True)
            self._cache.move_to_end(key)
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "size": len(self._cache),
            }
