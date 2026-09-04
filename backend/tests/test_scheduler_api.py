"""Scheduler endpoint tests (P2).

Proves the brief's flow end to end over HTTP: a failure is scheduled and
reported pending, and only becomes recovered — with a measured amount —
after the scheduler worker runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _payload(event_id: str, error_code: str = "INSUFFICIENT_FUNDS", amount: int = 149900):
    return {
        "event_id": event_id,
        "razorpay_payment_id": f"pay_{event_id}",
        "merchant_id": "merch_01",
        "customer_id": "cust_1",
        "type": "one_time",
        "amount": amount,
        "currency": "INR",
        "payment_method": "upi",
        "error_code": error_code,
        "error_description": "Insufficient funds",
        "failure_category": "insufficient_funds",
        "attempt_number": 1,
        "mandate_status": None,
        "timestamp": "2026-09-01T10:00:00Z",
    }


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=25)).isoformat()


class TestScheduledEndpoints:
    def test_processing_creates_a_pending_job(self):
        client.post("/api/dashboard/process", json=_payload("evt_api_sched_1"))

        resp = client.get("/api/dashboard/scheduled", params={"status": "pending"})
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert any(j["payment_id"] == "pay_evt_api_sched_1" for j in jobs)

    def test_listing_omits_the_raw_event_payload(self):
        client.post("/api/dashboard/process", json=_payload("evt_api_sched_2"))
        jobs = client.get("/api/dashboard/scheduled").json()["jobs"]
        assert jobs
        assert all("event_json" not in j for j in jobs)

    def test_worker_is_a_noop_before_the_cooldown(self):
        client.post("/api/dashboard/process", json=_payload("evt_api_sched_3"))
        resp = client.post("/api/dashboard/run-scheduled")
        assert resp.status_code == 200
        # Nothing is due yet: every job is 24h out.
        assert resp.json()["recovered"] == 0

    def test_scheduled_retry_recovers_after_worker_runs(self):
        """The migrated golden path: pending first, recovered after the run."""
        processed = client.post(
            "/api/dashboard/process", json=_payload("evt_api_sched_4")
        ).json()
        assert processed["final_outcome"] == "pending"
        assert processed["amount_recovered"] == 0

        run = client.post(
            "/api/dashboard/run-scheduled", params={"now": _later()}
        ).json()

        assert run["ran"] >= 1
        assert run["recovered"] >= 1
        # Money is only counted once the retry actually ran.
        assert run["amount_recovered"] >= 149900

    def test_rejects_an_unparseable_now(self):
        resp = client.post(
            "/api/dashboard/run-scheduled", params={"now": "not-a-timestamp"}
        )
        assert resp.status_code == 422

    def test_run_is_idempotent_across_repeated_calls(self):
        client.post("/api/dashboard/process", json=_payload("evt_api_sched_5"))
        when = _later()

        client.post("/api/dashboard/run-scheduled", params={"now": when})
        second = client.post(
            "/api/dashboard/run-scheduled", params={"now": when}
        ).json()

        # Everything already ran; a second pass recovers nothing further.
        assert second["recovered"] == 0
        assert second["amount_recovered"] == 0
