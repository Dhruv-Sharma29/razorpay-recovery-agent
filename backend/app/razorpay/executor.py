"""Razorpay test-mode executor.

Subclasses ``RecoveryExecutor`` to perform real (sandbox) recovery actions
against the Razorpay test-mode API.  All policy-gate checks, idempotency
tracking, and result construction are handled by the base class — this
module only implements ``_do_execute``.

What recovery actually means at Razorpay
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A failed payment is **terminal**.  There is no endpoint that retries one,
and ``/payments/:id/capture`` does not apply — capture only ever moves an
already-``authorized`` payment to ``captured``, and returns HTTP 400
("Payment is not in authorized state") for anything else.

So every recovery is a *new* payment attempt, and there are exactly two
ways to make one:

* **Charge a saved mandate** — ``POST /orders`` then
  ``POST /payments/create/recurring``.  Customer absent.  Requires a
  confirmed token from a prior authorised payment.  This is the only
  action that can move money without the customer doing anything.
* **Ask the customer to pay** — ``POST /payment_links`` with ``notify``,
  which makes Razorpay deliver the link by SMS and/or email.

Which of the two applies is decided by the policy action, never by this
module.  The executor translates a decision; it does not make one.

Safety invariants
~~~~~~~~~~~~~~~~~
1. **Test-key prefix guard**: refuses to run unless ``razorpay_key_id``
   starts with ``rzp_test_``.  A misconfigured ``.env`` pointing at live
   keys fails loudly rather than silently moving real money.
2. **Amount-cap guard**: re-checks ``auto_recovery_amount_limit`` before
   any network call, even though the policy engine already checked it.
   Defence in depth at the boundary that talks to a payment network.
3. **Missing prerequisites are not failures**: if an action needs a
   mandate token or a contact address and none is on file, the result
   says *not attempted*.  Reporting it as a failed payment would let a
   gap in the integration masquerade as a customer who declined, which
   would corrupt every recovery metric downstream.
4. **No credential logging**: only Razorpay reference ids are logged —
   never ``razorpay_key_secret``, and never full request bodies.
5. **No exceptions past _do_execute**: every path returns an
   ``ExecOutcome``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.executor.base import ExecOutcome, RecoveryExecutor, compute_idempotency_key
from app.models.payment_event import FailedTransactionEvent, RazorpayContext
from app.policy.result import PolicyAction, PolicyDecision

logger = logging.getLogger(__name__)

# Razorpay test-mode keys always have this prefix.
_TEST_KEY_PREFIX = "rzp_test_"

_API_ROOT = "https://api.razorpay.com/v1"

# Razorpay caps its own reference fields at 40 characters, so our SHA-256
# idempotency key has to be truncated to fit. The prefix of a digest is still
# collision-free for any realistic batch size.
_REFERENCE_MAX = 40

# Actions that move money without the customer present. Only these may charge
# a saved mandate.
_CHARGE_ACTIONS: frozenset[PolicyAction] = frozenset(
    {
        PolicyAction.SCHEDULED_RETRY,
        PolicyAction.IMMEDIATE_RETRY,
    }
)

# Actions whose whole point is to ask the customer to act. These become a
# payment link that Razorpay delivers.
_LINK_ACTIONS: frozenset[PolicyAction] = frozenset(
    {
        PolicyAction.TRIGGER_REAUTHORIZATION,
        PolicyAction.RESEND_AUTH_PROMPT,
        PolicyAction.SWITCH_PAYMENT_METHOD,
        PolicyAction.SEND_PAYMENT_REMINDER,
        PolicyAction.SEND_CHECKOUT_LINK,
    }
)


def _not_attempted(reason: str) -> ExecOutcome:
    """No request was sent, so nothing about the payment has changed.

    Distinct from a failure on purpose: ``payment_status="not_attempted"``
    keeps a missing integration out of the decline statistics.
    """
    return ExecOutcome(
        success=False,
        error=reason,
        payment_status="not_attempted",
        amount_recovered=0,
        simulated=False,
    )


class RazorpayTestExecutor(RecoveryExecutor):
    """Execute recovery actions against the Razorpay test-mode API.

    Policy-gate and idempotency logic live entirely in the base class;
    only ``_do_execute`` is overridden.
    """

    def __init__(self, state_store=None) -> None:
        super().__init__(state_store=state_store)
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )

    def close(self) -> None:
        """Shutdown the HTTP client cleanly."""
        self._client.close()

    # -- routing ----------------------------------------------------------

    def _do_execute(
        self,
        payment_event: FailedTransactionEvent,
        policy_decision: PolicyDecision,
        execution_id: str,
    ) -> ExecOutcome:
        """Translate the policy's action into the matching Razorpay call."""
        key_id = settings.razorpay_key_id

        # --- Guard 1: test-key prefix ---
        if not key_id.startswith(_TEST_KEY_PREFIX):
            logger.error(
                "RazorpayTestExecutor: key_id does not start with %s — "
                "refusing to execute to prevent accidental live-mode usage",
                _TEST_KEY_PREFIX,
            )
            return _not_attempted(
                f"Razorpay key_id must start with '{_TEST_KEY_PREFIX}' for "
                "test-mode execution. Refusing to run with what appears to "
                "be a live key."
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
            return _not_attempted(
                f"Amount {amount} exceeds auto_recovery_amount_limit {cap}. "
                "High-value payments must be handled manually."
            )

        action = policy_decision.action
        # Razorpay's own uniqueness constraints are the only idempotency the
        # payment path offers, so the base class's key is what we spend there.
        reference = compute_idempotency_key(payment_event, policy_decision)[
            :_REFERENCE_MAX
        ]

        if action in _CHARGE_ACTIONS:
            return self._charge_saved_mandate(
                payment_event, amount, reference, execution_id
            )
        if action in _LINK_ACTIONS:
            return self._send_payment_link(
                payment_event, policy_decision, amount, reference, execution_id
            )

        # Fail closed: an action with no Razorpay meaning is never improvised
        # into one that has.
        return _not_attempted(
            f"Action '{action.value}' has no Razorpay execution path; "
            "it requires manual handling."
        )

    # -- customer absent: charge the mandate -------------------------------

    def _charge_saved_mandate(
        self,
        payment_event: FailedTransactionEvent,
        amount: int,
        reference: str,
        execution_id: str,
    ) -> ExecOutcome:
        """Create a fresh order, then debit the saved token against it.

        A retry cannot reuse the failed payment's order — Razorpay maps an
        order 1:1 to a payment attempt — so a new one is created per attempt.
        """
        ctx = payment_event.razorpay or RazorpayContext()
        missing = [
            name
            for name, value in (
                ("token_id", ctx.token_id),
                ("customer_id", ctx.customer_id),
                ("email", ctx.email),
                ("contact", ctx.contact),
            )
            if not value
        ]
        if missing:
            # No mandate means no lawful unattended charge. Say so plainly
            # rather than letting it read as a decline.
            return _not_attempted(
                "Cannot charge unattended: no mandate on file for this "
                f"customer (missing {', '.join(missing)}). A payment link is "
                "the only available recovery path."
            )

        order, failure = self._post(
            "/orders",
            {
                "amount": amount,
                "currency": payment_event.currency,
                "payment_capture": 1,
                "receipt": reference,
            },
            payment_event.razorpay_payment_id,
        )
        if failure is not None:
            return failure

        order_id = order.get("id")
        if not order_id:
            return _not_attempted(
                "Razorpay accepted the order request but returned no order id."
            )

        payment, failure = self._post(
            "/payments/create/recurring",
            {
                "email": ctx.email,
                "contact": ctx.contact,
                "amount": amount,
                "currency": payment_event.currency,
                "order_id": order_id,
                "customer_id": ctx.customer_id,
                "token": ctx.token_id,
                "recurring": "1",
                "description": f"Recovery for {payment_event.razorpay_payment_id}",
            },
            payment_event.razorpay_payment_id,
        )
        if failure is not None:
            return failure

        status = payment.get("status", "unknown")
        rzp_id = payment.get("razorpay_payment_id") or payment.get("id", "unknown")
        logger.info(
            "RazorpayTestExecutor: charged mandate order=%s rzp_id=%s "
            "status=%s execution_id=%s",
            order_id,
            rzp_id,
            status,
            execution_id,
        )

        # Money counts only once the bank confirms it. File-based issuers park
        # the payment in `created` for hours, and calling that recovered would
        # be reporting revenue that has not arrived.
        captured = status == "captured"
        return ExecOutcome(
            success=True,
            error=None if captured else f"Debit submitted; status is '{status}'",
            payment_status=status,
            amount_recovered=amount if captured else 0,
            simulated=False,
        )

    # -- customer present: ask them to pay ---------------------------------

    def _send_payment_link(
        self,
        payment_event: FailedTransactionEvent,
        policy_decision: PolicyDecision,
        amount: int,
        reference: str,
        execution_id: str,
    ) -> ExecOutcome:
        """Create a payment link and have Razorpay deliver it."""
        ctx = payment_event.razorpay or RazorpayContext()
        if not ctx.email and not ctx.contact:
            return _not_attempted(
                "Cannot contact the customer: no email or phone on file, so "
                "Razorpay has nowhere to deliver the payment link."
            )

        body: dict[str, Any] = {
            "amount": amount,
            "currency": payment_event.currency,
            "reference_id": reference,
            "description": policy_decision.reason[:2048],
            "customer": {
                key: value
                for key, value in (
                    ("email", ctx.email),
                    ("contact", ctx.contact),
                )
                if value
            },
            # Razorpay sends it, so no separate email/SMS provider is needed.
            "notify": {"sms": bool(ctx.contact), "email": bool(ctx.email)},
            "reminder_enable": True,
        }

        link, failure = self._post(
            "/payment_links", body, payment_event.razorpay_payment_id
        )
        if failure is not None:
            return failure

        logger.info(
            "RazorpayTestExecutor: sent payment link plink=%s status=%s "
            "execution_id=%s",
            link.get("id", "unknown"),
            link.get("status", "unknown"),
            execution_id,
        )

        # The action succeeded; the money has not moved. Those are different
        # facts and the result reports both.
        return ExecOutcome(
            success=True,
            error=None,
            payment_status="link_sent",
            amount_recovered=0,
            simulated=False,
        )

    # -- transport ---------------------------------------------------------

    def _post(
        self, path: str, body: dict[str, Any], payment_id: str
    ) -> tuple[dict[str, Any], ExecOutcome | None]:
        """POST to Razorpay, returning ``(body, None)`` or ``({}, failure)``.

        Every network fault becomes a structured outcome. These are genuine
        failures — a request was sent — so unlike a missing prerequisite they
        are reported as such.
        """
        try:
            response = self._client.post(
                f"{_API_ROOT}{path}",
                json=body,
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            )
            response.raise_for_status()
            return response.json(), None

        except httpx.HTTPStatusError as exc:
            # Razorpay explains itself in the body; surface that, not a bare code.
            detail = ""
            try:
                detail = (
                    exc.response.json().get("error", {}).get("description", "")
                )
            except Exception:  # noqa: BLE001
                detail = ""
            logger.warning(
                "RazorpayTestExecutor: HTTP %d on %s for %s",
                exc.response.status_code,
                path,
                payment_id,
            )
            return {}, ExecOutcome(
                success=False,
                error=(
                    f"Razorpay {path} returned HTTP {exc.response.status_code}"
                    + (f": {detail}" if detail else "")
                ),
                payment_status="failed",
                amount_recovered=0,
                simulated=False,
            )

        except httpx.TimeoutException:
            logger.warning(
                "RazorpayTestExecutor: timeout on %s for %s", path, payment_id
            )
            return {}, ExecOutcome(
                success=False,
                error=f"Razorpay {path} request timed out",
                payment_status="failed",
                amount_recovered=0,
                simulated=False,
            )

        except httpx.ConnectError:
            logger.warning(
                "RazorpayTestExecutor: connection failed on %s for %s",
                path,
                payment_id,
            )
            return {}, ExecOutcome(
                success=False,
                error=f"Could not connect to Razorpay for {path}",
                payment_status="failed",
                amount_recovered=0,
                simulated=False,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RazorpayTestExecutor: unexpected error on %s for %s: %s",
                path,
                payment_id,
                exc,
            )
            return {}, ExecOutcome(
                success=False,
                error=f"Unexpected Razorpay error on {path}: {exc}",
                payment_status="failed",
                amount_recovered=0,
                simulated=False,
            )
