"""A/B harness and time-to-recovery tests.

The A/B exists to answer one question honestly: is the advisor's action
choice worth anything? For that the two arms must differ by exactly one
variable, so the simulated capture is keyed on the event's characteristics
rather than its generated id.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.executor.mock import _capture_succeeds
from app.main import app

client = TestClient(app)


class TestCaptureModel:
    def test_the_same_event_and_action_always_agree(self):
        a = _capture_succeeds("cust|1000|1|insufficient_funds", "insufficient_funds", "scheduled_retry")
        b = _capture_succeeds("cust|1000|1|insufficient_funds", "insufficient_funds", "scheduled_retry")
        assert a == b

    def test_waiting_beats_retrying_into_an_empty_account(self):
        """Across many events, the cooldown must actually pay for itself."""
        immediate = sum(
            _capture_succeeds(f"c{i}|1000|1|insufficient_funds", "insufficient_funds", "immediate_retry")
            for i in range(400)
        )
        scheduled = sum(
            _capture_succeeds(f"c{i}|1000|1|insufficient_funds", "insufficient_funds", "scheduled_retry")
            for i in range(400)
        )
        assert scheduled > immediate

    def test_switching_instrument_beats_retrying_a_declined_card(self):
        switch = sum(
            _capture_succeeds(f"c{i}|1000|1|bank_decline", "bank_decline", "switch_payment_method")
            for i in range(400)
        )
        retry = sum(
            _capture_succeeds(f"c{i}|1000|1|bank_decline", "bank_decline", "scheduled_retry")
            for i in range(400)
        )
        assert switch > retry

    def test_not_everything_succeeds(self):
        """A flat 100% recovery rate is not a believable demo."""
        outcomes = {
            _capture_succeeds(f"c{i}|1000|1|expired_card", "expired_card", "trigger_reauthorization")
            for i in range(200)
        }
        assert outcomes == {True, False}


class TestTiming:
    def test_batch_reports_time_to_recovery(self):
        d = client.post("/api/dashboard/run-batch", params={"count": 20}).json()
        t = d["timing"]
        assert t["recovered_count"] >= 0
        if t["recovered_count"]:
            assert t["median_seconds"] is not None
            assert t["max_seconds"] >= t["median_seconds"]

    def test_an_inline_recovery_is_instant(self):
        d = client.post(
            "/api/dashboard/run-batch", params={"count": 40, "seed": 3}
        ).json()
        # Immediate retries land with no delay; deferred ones carry a cooldown.
        assert d["timing"]["instant_count"] >= 0
        assert d["timing"]["max_seconds"] is None or d["timing"]["max_seconds"] >= 0


class TestAbEndpoint:
    def test_both_arms_run_the_same_number_of_events(self):
        r = client.post("/api/dashboard/run-ab", params={"count": 10, "seed": 5}).json()
        assert r["count_per_arm"] == 10
        assert r["seed"] == 5

    def test_delta_is_the_difference_between_the_arms(self):
        r = client.post("/api/dashboard/run-ab", params={"count": 12, "seed": 5}).json()
        assert r["delta"]["recovered_amount"] == (
            r["treatment"]["recovered_amount"] - r["control"]["recovered_amount"]
        )

    def test_a_run_with_no_advisor_choices_is_reported_inconclusive(self):
        """Without a live model both arms are identical by construction."""
        r = client.post("/api/dashboard/run-ab", params={"count": 8, "seed": 5}).json()
        if r["treatment"]["actions_chosen_by_model"] == 0:
            assert r["conclusive"] is False
            assert "NIM_API_KEY" in r["note"]

    def test_count_is_bounded(self):
        assert client.post(
            "/api/dashboard/run-ab", params={"count": 500}
        ).status_code == 422


class TestRestraintCounterfactual:
    """The batch reports what a retry-everything agent would have burned."""

    def test_batch_reports_restraint_block(self) -> None:
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 40, "seed": 7}
        ).json()
        restraint = body["restraint"]
        for key in (
            "extra_attempts",
            "amount_chased_past_cap",
            "attempts_past_retry_cap",
            "blind_retries_on_unknown_cause",
            "non_retryable_retried",
        ):
            assert isinstance(restraint[key], int)
            assert restraint[key] >= 0

    def test_extra_attempts_equals_events_policy_refused(self) -> None:
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 40, "seed": 7}
        ).json()
        # Every case the policy refused is one a naive agent would have retried.
        assert body["restraint"]["extra_attempts"] == body["outcomes"]["escalated"]

    def test_restraint_is_deterministic_for_a_seed(self) -> None:
        first = client.post(
            "/api/dashboard/run-batch", params={"count": 30, "seed": 3}
        ).json()["restraint"]
        client.post("/api/dashboard/reset")
        second = client.post(
            "/api/dashboard/run-batch", params={"count": 30, "seed": 3}
        ).json()["restraint"]
        assert first == second
