"""The advisor is given a real menu and real evidence.

Two gaps this closes: the model used to be offered the whole action enum
when policy would only ever permit one or two, and it was asked which
recovery action works best having never been told what any of them
recovered. Both are prompt-layer inputs — neither grants the model any
authority it did not already have.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.dashboard import _audit_logger
from app.main import app
from app.models.payment_event import FailureCategory
from app.policy.engine import RecoveryPolicyEngine
from app.recommendation.engine import _event_prompt

client = TestClient(app)


class TestPermittedActionMenu:
    def test_the_menu_is_the_policy_action_plus_its_alternatives(self) -> None:
        actions = RecoveryPolicyEngine.permitted_actions_for(
            FailureCategory.BANK_DECLINE
        )
        values = [a.value for a in actions]
        assert values[0] == "switch_payment_method"
        assert "scheduled_retry" in values

    def test_a_cause_with_one_answer_offers_no_choice(self) -> None:
        """Where policy has a single answer, there is nothing to advise on."""
        actions = RecoveryPolicyEngine.permitted_actions_for(
            FailureCategory.INSUFFICIENT_FUNDS
        )
        assert len(actions) == 1

    def test_an_unclassifiable_failure_offers_nothing(self) -> None:
        assert RecoveryPolicyEngine.permitted_actions_for(None) == ()

    def test_the_menu_never_contains_escalate(self) -> None:
        """Escalation is policy's to declare, never the advisor's to propose."""
        for category in FailureCategory:
            values = [
                a.value for a in RecoveryPolicyEngine.permitted_actions_for(category)
            ]
            assert "escalate" not in values, category


class TestPromptCarriesTheEvidence:
    def test_the_menu_reaches_the_model(self) -> None:
        prompt = json.loads(
            _event_prompt(
                _event(), None, None, ["switch_payment_method", "scheduled_retry"], []
            )
        )
        assert prompt["available_actions"] == [
            "switch_payment_method",
            "scheduled_retry",
        ]

    def test_measured_outcomes_reach_the_model(self) -> None:
        stats = [
            {
                "category": "insufficient_funds",
                "action": "scheduled_retry",
                "success_rate": 0.7,
                "attempts": 40,
            }
        ]
        prompt = json.loads(_event_prompt(_event(), None, None, [], stats))
        assert prompt["observed_outcomes"] == stats

    def test_absent_evidence_is_an_empty_list_not_a_missing_key(self) -> None:
        """A cold start must not produce a malformed prompt."""
        prompt = json.loads(_event_prompt(_event(), None, None))
        assert prompt["available_actions"] == []
        assert prompt["observed_outcomes"] == []


class TestOutcomeStatsAreMeasured:
    def test_rates_are_learned_from_the_audit_log(self) -> None:
        client.post("/api/dashboard/reset")
        client.post("/api/dashboard/run-batch", params={"count": 60, "seed": 11})
        stats = _audit_logger.outcome_stats(min_observations=2)
        assert stats, "a completed batch must leave measurable outcomes"
        for row in stats:
            assert 0.0 <= row["success_rate"] <= 1.0
            assert row["recovered"] <= row["attempts"]
            assert row["category"] and row["action"]

    def test_waiting_outperforms_retrying_in_the_measured_record(self) -> None:
        """The log should rediscover what the domain model encodes."""
        client.post("/api/dashboard/reset")
        client.post("/api/dashboard/run-batch", params={"count": 120, "seed": 5})
        rows = {
            (r["category"], r["action"]): r
            for r in _audit_logger.outcome_stats(min_observations=1)
        }
        scheduled = rows.get(("insufficient_funds", "scheduled_retry"))
        assert scheduled is not None
        # Not a coin flip: the cooldown has to be visibly earning its place.
        assert scheduled["success_rate"] > 0.5

    def test_a_thin_record_is_withheld_rather_than_trusted(self) -> None:
        client.post("/api/dashboard/reset")
        client.post("/api/dashboard/run-batch", params={"count": 60, "seed": 11})
        many = _audit_logger.outcome_stats(min_observations=1)
        few = _audit_logger.outcome_stats(min_observations=1000)
        assert many and not few


def _event():
    from datetime import datetime, timezone

    from app.models.payment_event import (
        FailedTransactionEvent,
        PaymentMethod,
        TransactionType,
    )

    return FailedTransactionEvent(
        event_id="evt_1",
        razorpay_payment_id="pay_1",
        merchant_id="m1",
        customer_id="c1",
        type=TransactionType.ONE_TIME,
        amount=100000,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code="X",
        error_description="y",
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
        attempt_number=1,
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
