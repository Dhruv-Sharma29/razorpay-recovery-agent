"""Money-metric tests for the evaluation harness (P1).

The brief's headline is measured money recovered across a batch, so these
pin exact attempted/recovered totals and the per-category breakdown rather
than just counts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.harness import Evaluator

# Two insufficient-funds events (recoverable) and one unknown (must escalate,
# recovering nothing). Amounts are distinct so a mis-bucketed total shows up.
FIXTURE = [
    {
        "event_id": "evt_money_1",
        "razorpay_payment_id": "pay_money_1",
        "merchant_id": "merch_01",
        "customer_id": "cust_1",
        "type": "one_time",
        "amount": 100000,
        "currency": "INR",
        "payment_method": "upi",
        "error_code": "INSUFFICIENT_FUNDS",
        "error_description": "Insufficient funds",
        "failure_category": "insufficient_funds",
        "attempt_number": 1,
        "mandate_status": None,
        "timestamp": "2026-09-01T10:00:00Z",
    },
    {
        "event_id": "evt_money_2",
        "razorpay_payment_id": "pay_money_2",
        "merchant_id": "merch_01",
        "customer_id": "cust_2",
        "type": "one_time",
        "amount": 250000,
        "currency": "INR",
        "payment_method": "upi",
        "error_code": "INSUFFICIENT_FUNDS",
        "error_description": "Insufficient funds",
        "failure_category": "insufficient_funds",
        "attempt_number": 1,
        "mandate_status": None,
        "timestamp": "2026-09-01T10:00:00Z",
    },
    {
        "event_id": "evt_money_3",
        "razorpay_payment_id": "pay_money_3",
        "merchant_id": "merch_01",
        "customer_id": "cust_3",
        "type": "one_time",
        "amount": 400000,
        "currency": "INR",
        "payment_method": "upi",
        "error_code": "TOTALLY_UNRECOGNISED",
        "error_description": "No rule matches this",
        "failure_category": "unknown",
        "attempt_number": 1,
        "mandate_status": None,
        "timestamp": "2026-09-01T10:00:00Z",
    },
]


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    path = tmp_path / "money.json"
    path.write_text(json.dumps(FIXTURE))
    return path


@pytest.fixture
def report(dataset: Path):
    return Evaluator().evaluate("money", dataset)


class TestMoneyMetrics:
    def test_attempted_amount_is_every_event(self, report):
        assert report.total_attempted_amount == 750000

    def test_recovered_amount_counts_only_captured_payments(self, report):
        # The two insufficient-funds events recover; unknown never does.
        assert report.total_recovered_amount == 350000

    def test_unknown_amount_is_escalated_not_recovered(self, report):
        assert report.amount_escalated == 400000

    def test_recovery_rate_by_amount(self, report):
        assert report.recovery_rate_by_amount == pytest.approx(350000 / 750000)

    def test_recovery_rate_by_count(self, report):
        assert report.recovery_rate_by_count == pytest.approx(2 / 3)

    def test_per_category_breakdown(self, report):
        funds = report.by_category["insufficient_funds"]
        assert funds.count == 2
        assert funds.recovered_count == 2
        assert funds.attempted_amount == 350000
        assert funds.recovered_amount == 350000
        assert funds.recovery_rate_amount == pytest.approx(1.0)

        unknown = report.by_category["unknown"]
        assert unknown.count == 1
        assert unknown.recovered_count == 0
        assert unknown.attempted_amount == 400000
        assert unknown.recovered_amount == 0
        assert unknown.recovery_rate_amount == 0.0

    def test_totals_reconcile_across_categories(self, report):
        assert (
            sum(b.attempted_amount for b in report.by_category.values())
            == report.total_attempted_amount
        )
        assert (
            sum(b.recovered_amount for b in report.by_category.values())
            == report.total_recovered_amount
        )

    def test_recovered_never_exceeds_attempted(self, report):
        assert report.total_recovered_amount <= report.total_attempted_amount

    def test_per_record_amounts_are_populated(self, report):
        by_id = {r.event_id: r for r in report.records}
        assert by_id["evt_money_1"].recovered_amount == 100000
        assert by_id["evt_money_1"].payment_status == "captured"
        assert by_id["evt_money_3"].recovered_amount == 0
