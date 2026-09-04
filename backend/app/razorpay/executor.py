"""Razorpay test-mode executor.

Subclasses ``RecoveryExecutor`` to perform real (sandbox) recovery
actions against the Razorpay test-mode API.  All policy-gate checks,
idempotency tracking, and result construction are handled by the base
class — this module only implements ``_do_execute``.

Safety invariants
~~~~~~~~~~~~~~~~~
1. **Test-key prefix guard**: Refuses to run if ``razorpay_key_id`` does
   not start with ``rzp_test_``.  A misconfigured ``.env`` pointing at
   live keys will fail loudly, not silently fall back.
2. **Amount-cap guard**: Re-checks ``auto_recovery_amount_limit`` before
   any network call, even though the policy engine already checked it.
   Defense-in-depth — this is the boundary that talks to a real (test)
   payment network.
3. **No credential logging**: Only the Razorpay provider reference ID is
   logged — never ``razorpay_key_secret`` or full request/response bodies.
4. **No exceptions past _do_execute**: Returns ``ExecOutcome`` on every
   path; the base class's try/except converts unexpected exceptions to
   ``ExecutionStatus.FAILED``.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.executor.base import ExecOutcome, RecoveryExecutor
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyDecision

logger = logging.getLogger(__name__)

# Razorpay test-mode keys always have this prefix.
_TEST_KEY_PREFIX = "rzp_test_"


class RazorpayTestExecutor(RecoveryExecutor):
    """Execute recovery actions against the Razorpay test-mode API.

    Subclasses ``RecoveryExecutor`` — policy-gate and idempotency logic
    are handled entirely by the base class.  Only ``_do_execute`` is
    overridden.

    The executor refuses to operate with non-test keys or amounts above
    the configured cap, and never logs credentials.
    """

    def __init__(self, state_store=None) -> None:
        super().__init__(state_store=state_store)
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )

    def close(self) -> None:
        """Shutdown the HTTP client cleanly."""
        self._client.close()

    def _do_execute(
        self,
        payment_event: FailedTransactionEvent,
        policy_decision: PolicyDecision,
        execution_id: str,
    ) -> ExecOutcome:
        """Execute a recovery action via the Razorpay test-mode API.

        Guards:
        1. Key must start with ``rzp_test_`` — refuse otherwise.
        2. Amount must be under ``auto_recovery_amount_limit`` — refuse otherwise.

        On success, returns the Razorpay-reported payment status and amount.
        On any failure, returns ``ExecOutcome(success=False, ...)`` instead
        of raising.
        """
        key_id = settings.razorpay_key_id
        key_secret = settings.razorpay_key_secret

        # --- Guard 1: test-key prefix ---
        if not key_id.startswith(_TEST_KEY_PREFIX):
            logger.error(
                "RazorpayTestExecutor: key_id does not start with %s — "
                "refusing to execute to prevent accidental live-mode usage",
                _TEST_KEY_PREFIX,
            )
            return ExecOutcome(
                success=False,
                error=(
                    f"Razorpay key_id must start with '{_TEST_KEY_PREFIX}' "
                    "for test-mode execution. Refusing to run with what "
                    "appears to be a live key."
                ),
                payment_status="not_attempted",
                amount_recovered=0,
            )

        # --- Guard 2: amount cap ---
        amount = payment_event.amount or 0
        cap = settings.auto_recovery_amount_limit
        if amount > cap:
            logger.warning(
                "RazorpayTestExecutor: amount %d exceeds cap %d — refusing",
                amount,
                cap,
            )
            return ExecOutcome(
                success=False,
                error=(
                    f"Amount {amount} exceeds auto_recovery_amount_limit "
                    f"{cap}. High-value payments must be handled manually."
                ),
                payment_status="not_attempted",
                amount_recovered=0,
            )

        # --- Razorpay API call ---
        action = policy_decision.action.value
        payment_id = payment_event.razorpay_payment_id

        try:
            # Attempt to capture the payment via Razorpay test-mode API.
            # https://razorpay.com/docs/api/payments/#capture-a-payment
            url = f"https://api.razorpay.com/v1/payments/{payment_id}/capture"
            payload = {
                "amount": amount,
                "currency": payment_event.currency,
            }

            response = self._client.post(
                url,
                json=payload,
                auth=(key_id, key_secret),
            )
            response.raise_for_status()
            body = response.json()

            rzp_status = body.get("status", "unknown")
            rzp_id = body.get("id", "unknown")
            recovered = body.get("amount", 0)

            # Log only the provider reference — never the secret.
            logger.info(
                "RazorpayTestExecutor: action=%s rzp_id=%s status=%s "
                "execution_id=%s",
                action,
                rzp_id,
                rzp_status,
                execution_id,
            )

            return ExecOutcome(
                success=rzp_status == "captured",
                error=None if rzp_status == "captured" else f"Razorpay status: {rzp_status}",
                payment_status=rzp_status,
                amount_recovered=recovered if rzp_status == "captured" else 0,
            )

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "RazorpayTestExecutor: HTTP %d for %s",
                exc.response.status_code,
                payment_id,
            )
            return ExecOutcome(
                success=False,
                error=f"Razorpay API returned HTTP {exc.response.status_code}",
                payment_status="failed",
                amount_recovered=0,
            )

        except httpx.TimeoutException:
            logger.warning(
                "RazorpayTestExecutor: timeout for %s", payment_id
            )
            return ExecOutcome(
                success=False,
                error="Razorpay API request timed out",
                payment_status="failed",
                amount_recovered=0,
            )

        except httpx.ConnectError:
            logger.warning(
                "RazorpayTestExecutor: connection failed for %s", payment_id
            )
            return ExecOutcome(
                success=False,
                error="Could not connect to Razorpay API",
                payment_status="failed",
                amount_recovered=0,
            )

        except Exception as exc:  # noqa: BLE001
            # Let the base class's try/except in execute() also catch this,
            # but provide a structured fallback here for completeness.
            logger.warning(
                "RazorpayTestExecutor: unexpected error for %s: %s",
                payment_id,
                exc,
            )
            return ExecOutcome(
                success=False,
                error=f"Unexpected Razorpay error: {exc}",
                payment_status="failed",
                amount_recovered=0,
            )
