"""Razorpay credential check.

Answers one question: would a real recovery call reach Razorpay right now?

Three failures currently look identical from the dashboard — wrong
credentials, no network, and a correctly-refused missing mandate. Only the
third is the system working as designed, so being able to rule the other two
out turns a debugging session into a single request.

The probe is ``GET /v1/payments?count=1``: authenticated, read-only, creates
nothing, and returns an empty collection rather than an error on a fresh test
account. Nothing here moves money or changes state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TEST_KEY_PREFIX = "rzp_test_"
_PROBE_URL = "https://api.razorpay.com/v1/payments"


def _mask(key_id: str) -> str:
    """Show enough to identify the key, never enough to reuse it.

    Key ids are not secret — they ship in checkout — but there is no reason
    for a health endpoint to hand one out in full.
    """
    if not key_id:
        return ""
    if len(key_id) <= 12:
        return f"{key_id[:4]}…"
    return f"{key_id[:12]}…{key_id[-2:]}"


def check_credentials(timeout: float = 10.0) -> dict[str, Any]:
    """Report whether Razorpay would accept our credentials.

    Never raises and never echoes the secret. Every branch names what to
    change, because "it didn't work" is the one answer that helps nobody.
    """
    now = datetime.now(timezone.utc).isoformat()
    key_id = settings.razorpay_key_id
    key_secret = settings.razorpay_key_secret

    base: dict[str, Any] = {
        "executor_mode": settings.executor_mode,
        "live_calls_enabled": settings.executor_mode == "razorpay_test",
        "key_configured": bool(key_id and key_secret),
        "key_id": _mask(key_id),
        "test_mode": key_id.startswith(_TEST_KEY_PREFIX),
        "checked_at": now,
    }

    if not key_id or not key_secret:
        return {
            **base,
            "status": "not_configured",
            "reachable": False,
            "detail": (
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are not both set. "
                "Add them to backend/.env from the Razorpay dashboard, with "
                "the mode toggle on Test."
            ),
        }

    if not key_id.startswith(_TEST_KEY_PREFIX):
        # Refused before any network call, exactly as the executor would.
        return {
            **base,
            "status": "live_key_refused",
            "reachable": False,
            "detail": (
                f"Key id does not start with '{_TEST_KEY_PREFIX}'. This looks "
                "like a live key, and live keys move real money — refusing to "
                "contact Razorpay with it."
            ),
        }

    try:
        response = httpx.get(
            _PROBE_URL,
            params={"count": 1},
            auth=(key_id, key_secret),
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=5.0, pool=5.0),
        )
    except httpx.TimeoutException:
        return {
            **base,
            "status": "unreachable",
            "reachable": False,
            "detail": (
                f"Razorpay did not respond within {timeout:.0f}s. This is a "
                "network problem, not a credential one."
            ),
        }
    except httpx.ConnectError:
        return {
            **base,
            "status": "unreachable",
            "reachable": False,
            "detail": (
                "Could not connect to api.razorpay.com. Check network access "
                "or a proxy; the credentials were never tested."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Razorpay credential check failed: %s", exc)
        return {
            **base,
            "status": "error",
            "reachable": False,
            "detail": f"Unexpected error contacting Razorpay: {exc}",
        }

    if response.status_code == 200:
        payments = 0
        try:
            payments = int(response.json().get("count", 0))
        except Exception:  # noqa: BLE001
            # A 200 already proves authentication; the body is a bonus.
            payments = 0
        return {
            **base,
            "status": "ok",
            "reachable": True,
            "payments_visible": payments,
            "detail": (
                "Razorpay accepted these credentials. "
                + (
                    "Live calls are enabled."
                    if base["live_calls_enabled"]
                    else "EXECUTOR_MODE is 'mock', so recoveries are still "
                    "simulated — set it to 'razorpay_test' to use them."
                )
            ),
        }

    if response.status_code in (401, 403):
        return {
            **base,
            "status": "unauthorized",
            "reachable": True,
            "detail": (
                f"Razorpay rejected these credentials (HTTP {response.status_code}). "
                "The key id and secret must be a matching pair from the same "
                "account and the same mode."
            ),
        }

    return {
        **base,
        "status": "error",
        "reachable": True,
        "detail": f"Razorpay returned HTTP {response.status_code} for the probe.",
    }
