"""Streaming batch tests.

The stream exists so a slow batch reads as work in progress rather than a
long silence. That only holds if every case appears exactly once and the
summary still matches the non-streaming endpoint.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _drain(count: int = 8, seed: int = 5, **extra) -> tuple[list, list]:
    """Read a stream to completion, returning (case frames, summary frames)."""
    cases: list[dict] = []
    summaries: list[dict] = []
    name: str | None = None
    with client.stream(
        "GET",
        "/api/dashboard/run-batch/stream",
        params={"count": count, "seed": seed, **extra},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
                if name == "case":
                    cases.append(payload)
                elif name == "summary":
                    summaries.append(payload)
    return cases, summaries


class TestBatchStream:
    def test_emits_one_frame_per_case_then_a_summary(self) -> None:
        cases, summaries = _drain(count=8)
        assert len(cases) == 8
        assert len(summaries) == 1
        assert summaries[0]["transactions_processed"] == 8

    def test_every_case_is_reported_exactly_once(self) -> None:
        cases, _ = _drain(count=12)
        assert sorted(case["index"] for case in cases) == list(range(1, 13))
        payment_ids = [case["payment_id"] for case in cases]
        assert len(set(payment_ids)) == len(payment_ids)

    def test_each_frame_carries_the_decision_for_that_case(self) -> None:
        cases, _ = _drain(count=8)
        for case in cases:
            assert case["total"] == 8
            assert case["category"], "the classifier's verdict must be present"
            assert case["action"], "the policy's action must be present"
            assert isinstance(case["allowed"], bool)
            assert isinstance(case["recovered"], bool)
            # A refusal must say why; an authorised action has nothing to explain.
            if case["allowed"]:
                assert case["escalation_reason"] is None
            else:
                assert case["escalation_reason"]

    def test_a_refused_case_is_never_reported_as_recovered(self) -> None:
        cases, _ = _drain(count=20)
        assert not [c for c in cases if c["recovered"] and not c["allowed"]]

    def test_the_summary_matches_the_plain_endpoint(self) -> None:
        """Streaming must change when you learn the result, not the result."""
        client.post("/api/dashboard/reset")
        _, summaries = _drain(count=10, seed=42)
        client.post("/api/dashboard/reset")
        plain = client.post(
            "/api/dashboard/run-batch", params={"count": 10, "seed": 42}
        ).json()
        for key in (
            "transactions_processed",
            "total_attempted_amount",
            "total_recoverable_amount",
            "total_recovered_amount",
            "outcomes",
            "restraint",
        ):
            assert summaries[0][key] == plain[key], key
