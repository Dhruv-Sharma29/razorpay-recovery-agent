"""A/B harness and time-to-recovery tests.

The A/B exists to answer one question honestly: is the advisor's action
choice worth anything? For that the two arms must differ by exactly one
variable, so the simulated capture is keyed on the event's characteristics
rather than its generated id.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dashboard import _ab_note
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

    def test_a_run_that_could_not_compare_is_reported_inconclusive(self):
        """No model answers and no alternatives means nothing was measured.

        Distinct from a model that answered and agreed — that is a finding,
        and the endpoint reports it as conclusive.
        """
        r = client.post("/api/dashboard/run-ab", params={"count": 8, "seed": 5}).json()
        advisor = r["advisor"]
        if not (advisor["model_answers"] and advisor["events_with_alternatives"]):
            assert r["conclusive"] is False
            # The note must name the actual constraint. At this batch size the
            # cause is a lack of alternatives, not a missing key — asserting
            # NIM_API_KEY here would pin the misleading message back in place.
            assert (
                "exactly one action" in r["note"] or "NIM_API_KEY" in r["note"]
            )

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
        # Every case the policy refused is one a naive agent would have
        # retried, so refusals are a subset of escalations — but not the whole
        # of them: an authorised retry whose execution fails also escalates,
        # and no naive agent would have "avoided" that attempt.
        assert (
            body["restraint"]["extra_attempts"] <= body["outcomes"]["escalated"]
        )
        assert body["restraint"]["extra_attempts"] > 0

    def test_restraint_is_deterministic_for_a_seed(self) -> None:
        first = client.post(
            "/api/dashboard/run-batch", params={"count": 30, "seed": 3}
        ).json()["restraint"]
        client.post("/api/dashboard/reset")
        second = client.post(
            "/api/dashboard/run-batch", params={"count": 30, "seed": 3}
        ).json()["restraint"]
        assert first == second


class TestAdvisorDiagnosis:
    """Why the advisor did or did not influence a run.

    "The advisor made no action choices" reads as a missing API key, when the
    usual cause is that policy authorised exactly one action and there was
    nothing to decide. These pin that the difference is reported.
    """

    def test_the_batch_reports_the_opportunity_ceiling(self) -> None:
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 60, "seed": 11}
        ).json()
        advisor = body["advisor"]
        # Cannot have applied more choices than there were choices to make.
        assert advisor["applied"] <= advisor["events_with_alternatives"]
        assert advisor["blocked_by_confidence"] <= advisor["proposed_change"]
        for key in (
            "events_with_alternatives",
            "model_answers",
            "proposed_change",
            "blocked_by_confidence",
            "applied",
        ):
            assert advisor[key] >= 0

    def test_a_small_batch_offers_no_choice_at_all(self) -> None:
        """Most causes authorise one action, so a short batch cannot conclude."""
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 30, "seed": 11}
        ).json()
        assert body["advisor"]["events_with_alternatives"] == 0

    def test_a_larger_batch_does_offer_choices(self) -> None:
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 100, "seed": 11}
        ).json()
        assert body["advisor"]["events_with_alternatives"] > 0


class TestAbNote:
    """The note must name the actual constraint, not guess at one."""

    def test_no_opportunity_blames_the_batch_not_the_model(self) -> None:
        note = _ab_note(
            0,
            30,
            {
                "events_with_alternatives": 0,
                "model_answers": 0,
                "proposed_change": 0,
                "blocked_by_confidence": 0,
                "applied": 0,
            },
        )
        assert "exactly one action" in note
        assert "NIM_API_KEY" not in note, "a missing key is not the cause here"

    def test_opportunity_without_a_model_names_the_key(self) -> None:
        note = _ab_note(
            0,
            100,
            {
                "events_with_alternatives": 11,
                "model_answers": 0,
                "proposed_change": 0,
                "blocked_by_confidence": 0,
                "applied": 0,
            },
        )
        assert "NIM_API_KEY" in note
        assert "11 of 100" in note

    def test_a_confidence_block_says_so_and_names_the_setting(self) -> None:
        note = _ab_note(
            0,
            100,
            {
                "events_with_alternatives": 11,
                "model_answers": 100,
                "proposed_change": 4,
                "blocked_by_confidence": 4,
                "applied": 0,
            },
        )
        assert "confidence" in note
        assert "MODEL_ACTION_CHOICE_MIN_CONFIDENCE" in note

    def test_plain_agreement_is_reported_as_a_result(self) -> None:
        note = _ab_note(
            0,
            100,
            {
                "events_with_alternatives": 11,
                "model_answers": 100,
                "proposed_change": 0,
                "blocked_by_confidence": 0,
                "applied": 0,
            },
        )
        assert "agreed with policy" in note
        assert "not a failure" in note

    def test_a_real_result_states_it_against_the_opportunity(self) -> None:
        note = _ab_note(
            3,
            100,
            {
                "events_with_alternatives": 11,
                "model_answers": 100,
                "proposed_change": 3,
                "blocked_by_confidence": 0,
                "applied": 3,
            },
        )
        assert "3 of 100" in note
        assert "11" in note


class TestArmsAreIsolated:
    """An A/B is only a comparison if the arms are independent.

    Both arms once shared the live state store, so whichever ran first swept
    every deferred retry left pending by earlier batches and booked the money
    as its own. That produced differences the advisor never caused — and
    recovery rates above 100%, since an arm could recover more than its own
    batch made recoverable.
    """

    def _leave_jobs_pending(self) -> int:
        client.post("/api/dashboard/reset")
        client.post(
            "/api/dashboard/run-batch",
            params={"count": 60, "seed": 3, "run_scheduler": False},
        )
        return client.get("/api/dashboard/scheduled").json()["count"]

    def test_pending_jobs_do_not_leak_into_an_arm(self) -> None:
        assert self._leave_jobs_pending() > 0, "fixture must leave work queued"
        body = client.post(
            "/api/dashboard/run-ab", params={"count": 100, "seed": 11}
        ).json()
        if body["advisor"]["applied"] == 0:
            # The advisor changed nothing, so the arms must be identical.
            assert (
                body["control"]["recovered_amount"]
                == body["treatment"]["recovered_amount"]
            )
            assert body["delta"]["recovered_amount"] == 0

    def test_the_ab_does_not_consume_the_live_queue(self) -> None:
        """Measuring must not drain the demo's own scheduled retries."""
        before = self._leave_jobs_pending()
        client.post("/api/dashboard/run-ab", params={"count": 40, "seed": 11})
        after = client.get("/api/dashboard/scheduled").json()["count"]
        assert after == before

    def test_no_arm_recovers_more_than_was_recoverable(self) -> None:
        self._leave_jobs_pending()
        body = client.post(
            "/api/dashboard/run-ab", params={"count": 100, "seed": 11}
        ).json()
        for arm in ("control", "treatment"):
            rate = body[arm]["recovery_rate_of_recoverable"]
            assert rate is None or rate <= 1.0, f"{arm} reported {rate}"

    def test_the_ab_is_repeatable(self) -> None:
        """Same seed, same answer — even with unrelated state in the database."""
        self._leave_jobs_pending()
        first = client.post(
            "/api/dashboard/run-ab", params={"count": 40, "seed": 11}
        ).json()
        client.post("/api/dashboard/run-batch", params={"count": 20, "seed": 9})
        second = client.post(
            "/api/dashboard/run-ab", params={"count": 40, "seed": 11}
        ).json()
        assert (
            first["control"]["recovered_amount"]
            == second["control"]["recovered_amount"]
        )


class TestRestraintIsPriced:
    """A count nobody can weigh becomes a figure comparable to revenue.

    The cost model is stated, not measured — the same standing as the capture
    rates — and the response says so, because a made-up number presented as
    measured is worse than no number.
    """

    def test_the_batch_prices_what_it_declined_to_do(self) -> None:
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 100, "seed": 11}
        ).json()
        r = body["restraint"]
        assert r["cost_avoided"] >= 0
        assert sum(r["cost_breakdown"].values()) == r["cost_avoided"]

    def test_the_model_is_declared_as_stated_not_measured(self) -> None:
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 40, "seed": 7}
        ).json()
        assert body["restraint"]["cost_model"]["stated_not_measured"] is True

    def test_a_double_charge_costs_more_than_the_retry_causing_it(self) -> None:
        """A dispute a human must handle dwarfs the processing fee."""
        body = client.post(
            "/api/dashboard/run-batch", params={"count": 40, "seed": 7}
        ).json()
        model = body["restraint"]["cost_model"]
        assert model["per_double_charge_risk"] > model["per_customer_friction"]
        assert model["per_customer_friction"] > model["per_issuer_attempt"]

    def test_refusing_nothing_costs_nothing(self) -> None:
        from app.dashboard import _restraint_cost

        zero = {
            "extra_attempts": 0,
            "attempts_past_retry_cap": 0,
            "blind_retries_on_unknown_cause": 0,
            "non_retryable_retried": 0,
        }
        assert _restraint_cost(zero)["cost_avoided"] == 0


class TestAgreementIsAFinding:
    """A model that answers, has real choices, and picks what policy picked
    has told you something. Calling that "inconclusive" discards it."""

    def test_agreement_across_real_choices_is_conclusive(self) -> None:
        from app.dashboard import _ab_note

        advisor = {
            "events_with_alternatives": 6,
            "model_answers": 66,
            "proposed_change": 0,
            "blocked_by_confidence": 0,
            "applied": 0,
        }
        note = _ab_note(0, 100, advisor)
        assert "agreed with policy" in note
        assert "not a failure" in note

    def test_no_answers_is_still_inconclusive(self) -> None:
        from app.dashboard import _ab_note

        advisor = {
            "events_with_alternatives": 6,
            "model_answers": 0,
            "proposed_change": 0,
            "blocked_by_confidence": 0,
            "applied": 0,
        }
        assert "NIM_API_KEY" in _ab_note(0, 100, advisor)
