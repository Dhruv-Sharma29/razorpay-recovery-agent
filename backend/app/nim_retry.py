"""Retry policy for NIM calls.

Measured against the live endpoint, roughly a third of calls at concurrency 5
came back ``503 Service Unavailable`` or timed out — the provider shedding
load, not the request being wrong. Those are exactly the failures worth
retrying, and retrying them is the difference between a demo where the model
answers and one where it silently falls back.

Deliberately narrow:

* Only ``429`` and ``5xx`` are retried. A ``400`` or ``401`` means the request
  or the key is wrong, and sending it again just wastes time.
* Backoff is exponential with jitter, because a fleet of workers retrying in
  lockstep is how a struggling endpoint is kept struggling.
* The budget is small. This sits in a payment pipeline where the fallback is
  safe and immediate; waiting a long time for advisory text is the wrong
  trade.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Seconds before the first retry; doubled each time, plus jitter.
BASE_DELAY = 0.75
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def is_retryable(exc: Exception) -> bool:
    """True for a fault that a later identical request might survive."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def with_retries(
    call: Callable[[], T],
    *,
    label: str,
    max_retries: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``call``, retrying transient upstream failures.

    Re-raises the final exception rather than swallowing it, so each caller
    keeps its own structured fallback and the failure is still reported
    honestly as a fallback rather than a success.
    """
    budget = settings.nim_max_retries if max_retries is None else max_retries
    attempt = 0
    while True:
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            if attempt >= budget or not is_retryable(exc):
                raise
            # Full jitter: spread retries out instead of synchronising every
            # worker onto the same instant.
            delay = random.uniform(0, BASE_DELAY * (2**attempt))
            attempt += 1
            logger.info(
                "%s: retrying after %s (attempt %d of %d) in %.2fs",
                label,
                type(exc).__name__,
                attempt,
                budget,
                delay,
            )
            sleep(delay)
