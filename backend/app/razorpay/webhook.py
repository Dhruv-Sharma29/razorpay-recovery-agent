"""Razorpay webhook ingestion.

Turns a live ``payment.failed`` notification into the event the pipeline
already understands.

Why this is not simply "parse and process"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Three facts about Razorpay's webhooks shape everything here:

1. **The signature covers the raw body.** Parsing the JSON and re-serialising
   it changes the bytes and breaks verification, so the raw payload has to be
   verified before anything else touches it.
2. **The secret is the webhook secret**, set when creating the webhook — not
   ``razorpay_key_secret``. Using the wrong one fails every request.
3. **``payment.failed`` is not final.** A payment can fail and then be
   captured moments later — common on UPI, where the customer retries
   themselves. Acting on the failure alone would chase a customer who has
   already paid, so a payload that reports the payment as captured is
   accepted and ignored rather than processed.

A payment that fails during first-attempt authorisation may produce no
webhook at all, so this is one failure signal among several, never the only
one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    RazorpayContext,
    TransactionType,
)

logger = logging.getLogger(__name__)

_FAILED_EVENT = "payment.failed"

# Statuses that mean the money is already in, so no recovery is warranted.
_SETTLED_STATUSES = frozenset({"captured", "authorized", "refunded"})


class WebhookRejected(Exception):
    """The payload is not a trustworthy Razorpay notification."""


def verify_signature(raw_body: bytes, signature: str | None) -> None:
    """Reject anything not signed with the configured webhook secret.

    Compared in constant time. An unset secret is treated as a refusal, not
    as "skip the check" — an endpoint that silently accepts unsigned input
    would let anyone inject payment events into the pipeline.
    """
    secret = settings.razorpay_webhook_secret
    if not secret:
        raise WebhookRejected(
            "razorpay_webhook_secret is not configured; refusing to accept "
            "unverified webhook payloads."
        )
    if not signature:
        raise WebhookRejected("Missing X-Razorpay-Signature header")

    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookRejected("Signature does not match the request body")


def _payment_entity(payload: dict[str, Any]) -> dict[str, Any]:
    return (
        payload.get("payload", {})
        .get("payment", {})
        .get("entity", {})
    )


def _method(raw: str | None) -> PaymentMethod:
    """Map Razorpay's method string onto ours, defaulting rather than failing.

    An unrecognised method must not drop a real failed payment on the floor;
    the classifier keys on the error code, not the instrument.
    """
    try:
        return PaymentMethod(str(raw or "").lower())
    except ValueError:
        return PaymentMethod.CARD


def parse_failed_payment(raw_body: bytes) -> FailedTransactionEvent | None:
    """Build a pipeline event, or return None when there is nothing to do.

    None is returned for events this system should not act on: a different
    webhook type, or a payment that has already settled. That is a normal
    outcome, not an error — Razorpay must still receive a 200 so it stops
    redelivering.
    """
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise WebhookRejected(f"Body is not valid JSON: {exc}") from exc

    event_name = payload.get("event")
    if event_name != _FAILED_EVENT:
        logger.info("Webhook: ignoring event %s", event_name)
        return None

    entity = _payment_entity(payload)
    payment_id = entity.get("id")
    if not payment_id:
        raise WebhookRejected("payment.failed payload carried no payment id")

    status = str(entity.get("status", "")).lower()
    if status in _SETTLED_STATUSES:
        # Late authorisation or a customer-driven retry. Chasing this would
        # dun someone who has already paid.
        logger.info(
            "Webhook: %s reports status=%s; no recovery warranted",
            payment_id,
            status,
        )
        return None

    error = entity.get("error_code") or entity.get("error_reason") or "UNKNOWN"
    created = entity.get("created_at")
    timestamp = (
        datetime.fromtimestamp(created, tz=timezone.utc)
        if isinstance(created, (int, float))
        else datetime.now(timezone.utc)
    )

    notes = entity.get("notes") or {}
    return FailedTransactionEvent(
        event_id=f"evt_wh_{payment_id}",
        razorpay_payment_id=str(payment_id),
        merchant_id=str(notes.get("merchant_id") or "razorpay_webhook"),
        customer_id=str(entity.get("customer_id") or notes.get("customer_id") or "unknown"),
        type=(
            TransactionType.SUBSCRIPTION
            if entity.get("invoice_id") or entity.get("token_id")
            else TransactionType.ONE_TIME
        ),
        amount=int(entity.get("amount") or 0),
        currency=str(entity.get("currency") or "INR"),
        payment_method=_method(entity.get("method")),
        error_code=str(error),
        error_description=str(
            entity.get("error_description") or "Reported by Razorpay webhook"
        ),
        # Self-reported and never trusted: the classifier decides the category
        # from the error code independently.
        failure_category=FailureCategory.UNKNOWN,
        attempt_number=1,
        razorpay=RazorpayContext(
            customer_id=entity.get("customer_id"),
            token_id=entity.get("token_id"),
            email=entity.get("email"),
            contact=entity.get("contact"),
        ),
        timestamp=timestamp,
    )
