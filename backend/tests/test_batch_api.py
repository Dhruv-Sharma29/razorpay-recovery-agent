"""Batch runner tests (P3).

The batch endpoint is the demo's headline: process N fresh failures, run
deferred retries to completion, and report measured money recovered.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _run(**params):
    resp = client.post("/api/dashboard/run-batch", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestRunBatch:
    def test_processes_the_requested_count(self):
        data = _run(count=10)
        assert data["transactions_processed"] == 10

    def test_reports_measured_money(self):
        data = _run(count=15)
        assert data["total_attempted_amount"] > 0
        assert data["total_recovered_amount"] >= 0
        # Cannot recover more than was ever at stake.
        assert data["total_recovered_amount"] <= data["total_attempted_amount"]

    def test_rate_matches_the_amounts(self):
        data = _run(count=12)
        expected = data["total_recovered_amount"] / data["total_attempted_amount"]
        assert data["recovery_rate_by_amount"] == expected

    def test_funnel_stages_narrow_monotonically(self):
        f = _run(count=25)["funnel"]
        assert f["raw"] >= f["needed_signal"] >= f["confirmed_recovered"]
        assert f["contacted"] >= f["confirmed_recovered"]

    def test_scenario_totals_reconcile_with_the_batch(self):
        data = _run(count=20, run_scheduler=False)
        assert (
            sum(s["attempted_amount"] for s in data["by_scenario"])
            == data["total_attempted_amount"]
        )

    def test_scheduler_runs_deferred_retries(self):
        data = _run(count=20, run_scheduler=True)
        assert data["scheduler"] is not None
        # Deferred retries recover money only once the worker runs them.
        assert data["scheduler"]["amount_recovered"] >= 0

    def test_skipping_the_scheduler_leaves_work_pending(self):
        data = _run(count=20, run_scheduler=False)
        assert data["scheduler"] is None

    def test_runs_accumulate_rather_than_colliding(self):
        """Fresh ids per run are what make the button repeatable."""
        first = _run(count=5)
        second = _run(count=5)
        assert set(first["audit_ids"]).isdisjoint(second["audit_ids"])

    def test_seed_makes_a_batch_reproducible(self):
        a = _run(count=8, seed=99, run_scheduler=False)
        b = _run(count=8, seed=99, run_scheduler=False)
        assert a["total_attempted_amount"] == b["total_attempted_amount"]

    def test_scenario_recovery_reconciles_with_the_batch_total(self):
        """The per-scenario card must never contradict the headline number.

        Deferred retries recover after the main loop, so their amounts have
        to be attributed back to the scenario they came from.
        """
        data = _run(count=30, run_scheduler=True)
        assert (
            sum(s["recovered_amount"] for s in data["by_scenario"])
            == data["total_recovered_amount"]
        )

    def test_confirmed_recovered_matches_scenario_counts(self):
        data = _run(count=30, run_scheduler=True)
        assert data["funnel"]["confirmed_recovered"] == sum(
            s["recovered_count"] for s in data["by_scenario"]
        )

    def test_result_is_labelled_simulated(self):
        assert _run(count=3)["simulated"] is True

    def test_count_guard_rejects_zero(self):
        assert client.post(
            "/api/dashboard/run-batch", params={"count": 0}
        ).status_code == 422

    def test_count_guard_rejects_over_500(self):
        assert client.post(
            "/api/dashboard/run-batch", params={"count": 501}
        ).status_code == 422


class TestExplainMode:
    """Reasoning is advisory, so skipping it must not move a single number.

    This is what justifies `explain=false` by default: a batch would
    otherwise pay one live LLM round trip per event for text nobody reads,
    which is what made the UI time out.
    """

    METRICS = [
        "transactions_processed",
        "total_attempted_amount",
        "total_recovered_amount",
        "recovery_rate_by_amount",
        "outcomes",
        "funnel",
    ]

    def test_explain_does_not_change_any_batch_metric(self):
        off = _run(count=12, seed=7, run_scheduler=True, explain=False)
        on = _run(count=12, seed=7, run_scheduler=True, explain=True)
        for key in self.METRICS:
            assert off[key] == on[key], f"{key} changed with explain=true"

    def test_default_skips_live_reasoning(self):
        assert _run(count=4)["reasoning"]["mode"] == "skipped"

    def test_explain_true_uses_the_model_path(self):
        assert _run(count=4, explain=True)["reasoning"]["mode"] == "model"

    def test_reasoning_block_names_the_model(self):
        assert "nemotron" in _run(count=3)["reasoning"]["model"]


class TestProvider:
    def test_reports_the_configured_provider_without_leaking_the_key(self):
        resp = client.get("/api/dashboard/provider")
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "nvidia-nim"
        assert "nemotron" in data["model"]
        assert isinstance(data["configured"], bool)
        # The key itself must never be returned.
        assert "key" not in json_dumps_lower(data)


def json_dumps_lower(data) -> str:
    import json

    return json.dumps(data).lower()


class TestReset:
    def test_clears_recovery_state(self):
        _run(count=5)
        resp = client.post("/api/dashboard/reset")
        assert resp.status_code == 200
        assert resp.json()["recovery_state_cleared"] is True

    def test_scheduled_jobs_are_cleared(self):
        _run(count=10, run_scheduler=False)
        client.post("/api/dashboard/reset")
        jobs = client.get("/api/dashboard/scheduled").json()["jobs"]
        assert jobs == []

    def test_audit_history_survives_reset(self):
        """Append-only means a reset must never erase the record."""
        _run(count=5)
        before = client.get("/api/dashboard/audit").json()["count"]
        client.post("/api/dashboard/reset")
        after = client.get("/api/dashboard/audit").json()["count"]
        assert after >= before
