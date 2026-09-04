"""Reasoning explanation cache.

Explanations for the same policy outcome on the same failure mode are
deterministic given the prompt structure, but the LLM API is slow and
expensive. We cache successful explanations keyed by the exact rule
tuple that fired.

The cache key explicitly excludes PII (customer ID), transient identifiers
(payment ID, event ID), and amounts.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from threading import Lock

from app.classifier.result import ClassificationResult
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyDecision
from app.reasoning.result import ReasoningResult

logger = logging.getLogger(__name__)


class ExplanationCache:
    """Thread-safe LRU cache for reasoning explanations.

    Keys are derived from the deterministic rules that fired, not the
    payment event details.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._cache: OrderedDict[str, ReasoningResult] = OrderedDict()
        self._lock = Lock()
        
        self.hits = 0
        self.misses = 0

    def _build_key(
        self,
        event: FailedTransactionEvent,
        classification: ClassificationResult,
        policy: PolicyDecision,
    ) -> str:
        """Build a cache key from the rule tuple.
        
        Excludes PII and amounts.
        """
        # category is safe; it's a fixed enum. rule_ids map to exact rules.
        cat = classification.category.value if classification else "unknown"
        c_rule = classification.rule_id if classification else "unknown"
        p_rule = policy.rule_id if policy else "unknown"
        p_act = policy.action.value if policy else "unknown"
        
        return f"{cat}:{c_rule}:{p_rule}:{p_act}"

    def get(
        self,
        event: FailedTransactionEvent,
        classification: ClassificationResult,
        policy: PolicyDecision,
    ) -> ReasoningResult | None:
        """Retrieve a cached explanation if one exists for this rule tuple."""
        key = self._build_key(event, classification, policy)
        with self._lock:
            if key in self._cache:
                self.hits += 1
                # Move to end to show it was recently used (LRU)
                self._cache.move_to_end(key)
                # Return a copy to prevent mutation, set from_cache flag
                result = self._cache[key].model_copy()
                result.from_cache = True
                return result
            self.misses += 1
            return None

    def put(
        self,
        event: FailedTransactionEvent,
        classification: ClassificationResult,
        policy: PolicyDecision,
        result: ReasoningResult,
    ) -> None:
        """Store a successful explanation in the cache."""
        if not result.success or result.is_fallback:
            return

        key = self._build_key(event, classification, policy)
        with self._lock:
            # Store a copy without the from_cache flag set to True
            store_val = result.model_copy()
            store_val.from_cache = False
            self._cache[key] = store_val
            self._cache.move_to_end(key)
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
