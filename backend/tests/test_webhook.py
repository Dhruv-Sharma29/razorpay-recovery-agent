"""Razorpay webhook ingestion tests.

The endpoint accepts input from the public internet, so the signature check
is the whole security boundary. The other tests pin the two Razorpay
behaviours that would otherwise cause real harm: an unsigned payload being
trusted, and a customer being chased for a payment they already made.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.razorpay.webhook import WebhookRejected, parse_failed_payment

client = TestClient(app)

SECRET = "whsec_test_123"


def _body(**over) -> bytes:
    entity = {
        "id": "pay_live_001",
        "amount": 149900,
        "currency": "INR",
        "status": "failed",
        "method": "upi",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Insufficient balance",
        "email": "customer@example.com",
        "contact": "+919999999999",
        "customer_id": "cust_live_001",
        "created_at": 1789000000,
        **over,
    }
    return json.dumps(
        {"event": over.pop("event", "payment.failed"), "payload": {"payment": {"entity": entity}}}
    ).encode("utf-8")


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _post(raw: bytes, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Razorpay-Signature"] = signature
    return client.post("/api/dashboard/webhook/razorpay", content=raw, headers=headers)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_webhook_secret", SECRET)


class TestSignatureIsTheBoundary:
    def test_a_correctly_signed_payload_is_processed(self) -> None:
        raw = _body()
        response = _post(raw, _sign(raw))
        assert response.status_code == 200
        assert response.json()["processed"] is True

    def test_an_unsigned_payload_is_refused(self) -> None:
        raw = _body()
        assert _post(raw, None).status_code == 400

    def test_a_wrong_signature_is_refused(self) -> None:
        raw = _body()
        assert _post(raw, _sign(raw, "not_the_secret")).status_code == 400

    def test_a_tampered_body_is_refused(self) -> None:
        """The signature must cover the bytes, not merely be present."""
        raw = _body()
        signature = _sign(raw)
        tampered = raw.replace(b'"amount": 149900', b'"amount": 100')
        assert tampered != raw
        assert _post(tampered, signature).status_code == 400

    def test_an_unconfigured_secret_refuses_everything(self, monkeypatch) -> None:
        """Missing config must fail closed, never skip the check."""
        monkeypatch.setattr(settings, "razorpay_webhook_secret", "")
        raw = _body()
        assert _post(raw, _sign(raw)).status_code == 400


class TestNotEveryNotificationIsWork:
    def test_a_settled_payment_is_acknowledged_but_not_chased(self) -> None:
        """payment.failed can be followed by capture; chasing would dun them."""
        raw = _body(status="captured")
        response = _post(raw, _sign(raw))
        assert response.status_code == 200
        assert response.json()["processed"] is False

    def test_an_unrelated_event_is_acknowledged(self) -> None:
        raw = _body(event="payment.captured")
        response = _post(raw, _sign(raw))
        # 200, so Razorpay stops redelivering something we will never act on.
        assert response.status_code == 200
        assert response.json()["processed"] is False

    def test_malformed_json_is_rejected_not_crashed(self) -> None:
        raw = b"{not json"
        assert _post(raw, _sign(raw)).status_code == 400


class TestPayloadMapping:
    def test_gateway_identifiers_are_carried_onto_the_event(self) -> None:
        event = parse_failed_payment(_body())
        assert event is not None
        assert event.razorpay is not None
        assert event.razorpay.customer_id == "cust_live_001"
        assert event.razorpay.email == "customer@example.com"
        assert event.razorpay.contact == "+919999999999"

    def test_the_self_reported_category_is_never_trusted(self) -> None:
        """Classification is the classifier's job, from the error code."""
        event = parse_failed_payment(_body())
        assert event is not None
        assert event.failure_category.value == "unknown"

    def test_a_payload_without_a_payment_id_is_rejected(self) -> None:
        raw = json.dumps(
            {"event": "payment.failed", "payload": {"payment": {"entity": {}}}}
        ).encode("utf-8")
        with pytest.raises(WebhookRejected):
            parse_failed_payment(raw)

    def test_an_unknown_method_does_not_drop_the_payment(self) -> None:
        event = parse_failed_payment(_body(method="wallet_xyz"))
        assert event is not None
        assert event.razorpay_payment_id == "pay_live_001"
