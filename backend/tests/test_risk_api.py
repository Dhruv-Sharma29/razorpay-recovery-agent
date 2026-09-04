"""Revenue-at-risk aggregation tests.

Read-only rollups over the audit log. Must never expose the raw customer
id, and must not claim more recovered than was ever at stake.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _event(event_id, *, amount=149900, ttype="one_time", mandate=None,
           customer="cust_1", merchant="merch_01", code="INSUFFICIENT_FUNDS"):
    return {
        "event_id": event_id,
        "razorpay_payment_id": f"pay_{event_id}",
        "merchant_id": merchant,
        "customer_id": customer,
        "type": ttype,
        "amount": amount,
        "currency": "INR",
        "payment_method": "upi",
        "error_code": code,
        "error_description": "failure",
        "failure_category": "unknown",
        "attempt_number": 1,
        "mandate_status": mandate,
        "timestamp": "2026-09-01T10:00:00Z",
    }


def _risk(limit: int = 50):
    """The audit log is shared across the suite, so ask for enough rows
    that this test's own merchant is not crowded out of the ranking."""
    resp = client.get("/api/dashboard/risk", params={"limit": limit})
    assert resp.status_code == 200
    return resp.json()


class TestRevenueAtRisk:
    def test_aggregates_amount_at_risk_per_merchant(self):
        client.post("/api/dashboard/process", json=_event("evt_risk_m1", merchant="merch_risk"))
        data = _risk()
        row = next(
            (m for m in data["by_merchant"] if m["merchant_id"] == "merch_risk"),
            None,
        )
        assert row is not None
        assert row["at_risk_amount"] >= 149900

    def test_outstanding_never_exceeds_at_risk(self):
        client.post("/api/dashboard/process", json=_event("evt_risk_o1"))
        data = _risk()
        assert data["outstanding_amount"] <= data["total_at_risk_amount"]
        assert data["total_recovered_amount"] <= data["total_at_risk_amount"]

    def test_counts_repeat_failures_for_one_customer(self):
        for i in range(3):
            client.post(
                "/api/dashboard/process",
                json=_event(f"evt_risk_rep_{i}", customer="cust_repeat_xyz"),
            )
        repeats = _risk()["repeat_customers"]
        assert any(c["failures"] >= 3 for c in repeats)

    def test_never_exposes_the_raw_customer_id(self):
        """customer_id is PII and is deliberately never persisted."""
        client.post(
            "/api/dashboard/process",
            json=_event("evt_risk_pii", customer="cust_secret_pii"),
        )
        blob = client.get("/api/dashboard/risk").text
        assert "cust_secret_pii" not in blob
        # It is grouped by a pseudonymous reference instead.
        assert "cref_" in blob or _risk()["repeat_customers"] == []

    def test_flags_subscription_mandate_problems(self):
        client.post(
            "/api/dashboard/process",
            json=_event(
                "evt_risk_sub",
                ttype="subscription",
                mandate="expired",
                code="EXPIRED_CARD",
            ),
        )
        sub = _risk()["subscription_failures"]
        assert sub["count"] >= 1
        assert sub["mandate_issues"] >= 1

    def test_limit_is_bounded(self):
        assert client.get(
            "/api/dashboard/risk", params={"limit": 0}
        ).status_code == 422
        assert client.get(
            "/api/dashboard/risk", params={"limit": 999}
        ).status_code == 422
