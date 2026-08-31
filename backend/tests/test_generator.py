"""Comprehensive tests for the synthetic dataset generator.

Tests cover:
- Deterministic generation (same seed → same output)
- Required fields exist on every record
- All expected failure categories are represented
- Development / held-out split is deterministic
- No transaction ID overlap between dev and held-out
- Generated amounts, timestamps, and IDs are valid
- Generator can be run repeatedly without corrupting output
- Edge cases (seed variation, total bounds)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.ingestion.generator import (
    DEFAULT_SEED,
    TOTAL_EVENTS,
    generate_and_write,
    generate_dataset,
    split_dataset,
    write_dataset,
)
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)


# ---------------------------------------------------------------------------
# Required fields that must be present on every event
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = {
    "event_id",
    "razorpay_payment_id",
    "merchant_id",
    "customer_id",
    "type",
    "amount",
    "currency",
    "payment_method",
    "error_code",
    "error_description",
    "failure_category",
    "attempt_number",
    "mandate_status",
    "timestamp",
}

EXPECTED_CATEGORIES = {
    "insufficient_funds",
    "expired_card",
    "network_error",
    "bank_decline",
    "authentication_failure",
    "unknown",
}


# ===================================================================
# 1. Deterministic generation
# ===================================================================


class TestDeterministicGeneration:
    """Same seed must produce identical datasets."""

    def test_same_seed_same_events(self) -> None:
        events_a = generate_dataset(seed=42)
        events_b = generate_dataset(seed=42)
        assert len(events_a) == len(events_b)
        for a, b in zip(events_a, events_b):
            assert a.model_dump() == b.model_dump()

    def test_same_seed_same_json(self) -> None:
        events_a = generate_dataset(seed=42)
        events_b = generate_dataset(seed=42)
        json_a = [e.model_dump_json() for e in events_a]
        json_b = [e.model_dump_json() for e in events_b]
        assert json_a == json_b

    def test_different_seed_different_events(self) -> None:
        events_a = generate_dataset(seed=42)
        events_b = generate_dataset(seed=99)
        ids_a = {e.event_id for e in events_a}
        ids_b = {e.event_id for e in events_b}
        # Event IDs are sequential so they match, but content differs
        assert ids_a == ids_b  # same naming scheme
        # At least some payment IDs must differ (randomized)
        pay_ids_a = [e.razorpay_payment_id for e in events_a]
        pay_ids_b = [e.razorpay_payment_id for e in events_b]
        assert pay_ids_a != pay_ids_b

    def test_deterministic_across_ten_runs(self) -> None:
        """Generate 10 times in a row — all must match."""
        reference = [e.model_dump() for e in generate_dataset(seed=7)]
        for _ in range(9):
            current = [e.model_dump() for e in generate_dataset(seed=7)]
            assert current == reference


# ===================================================================
# 2. Required fields
# ===================================================================


class TestRequiredFields:
    """Every record must contain all fields needed by the architecture."""

    @pytest.fixture()
    def events(self) -> list[FailedTransactionEvent]:
        return generate_dataset(seed=42)

    def test_all_fields_present(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            dumped = event.model_dump()
            assert REQUIRED_FIELDS.issubset(
                dumped.keys()
            ), f"Missing fields in {event.event_id}: {REQUIRED_FIELDS - dumped.keys()}"

    def test_event_id_format(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            assert event.event_id.startswith("evt_synth_")
            # Numeric suffix
            suffix = event.event_id.split("_")[-1]
            assert suffix.isdigit()

    def test_payment_id_format(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            assert event.razorpay_payment_id.startswith("pay_test_")

    def test_merchant_id_format(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            assert event.merchant_id.startswith("merch_synth_")

    def test_customer_id_format(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            assert event.customer_id.startswith("cust_synth_")

    def test_currency_is_inr(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            assert event.currency == "INR"


# ===================================================================
# 3. Failure categories
# ===================================================================


class TestFailureCategories:
    """All expected categories must be represented in the dataset."""

    @pytest.fixture()
    def events(self) -> list[FailedTransactionEvent]:
        return generate_dataset(seed=42)

    def test_all_categories_present(self, events: list[FailedTransactionEvent]) -> None:
        categories = {e.failure_category.value for e in events}
        assert categories == EXPECTED_CATEGORIES

    def test_each_category_has_multiple_events(
        self, events: list[FailedTransactionEvent]
    ) -> None:
        from collections import Counter

        counts = Counter(e.failure_category.value for e in events)
        for cat in EXPECTED_CATEGORIES:
            assert counts[cat] >= 2, f"Category {cat} has too few events: {counts[cat]}"

    def test_failure_category_is_valid_enum(
        self, events: list[FailedTransactionEvent]
    ) -> None:
        valid = {fc.value for fc in FailureCategory}
        for event in events:
            assert event.failure_category.value in valid


# ===================================================================
# 4. Development / held-out split
# ===================================================================


class TestSplit:
    """Split must be deterministic and non-overlapping."""

    @pytest.fixture()
    def events(self) -> list[FailedTransactionEvent]:
        return generate_dataset(seed=42)

    def test_split_deterministic(self, events: list[FailedTransactionEvent]) -> None:
        dev_a, ho_a = split_dataset(events, seed=42)
        dev_b, ho_b = split_dataset(events, seed=42)
        assert [e.event_id for e in dev_a] == [e.event_id for e in dev_b]
        assert [e.event_id for e in ho_a] == [e.event_id for e in ho_b]

    def test_no_id_overlap(self, events: list[FailedTransactionEvent]) -> None:
        dev, ho = split_dataset(events, seed=42)
        dev_ids = {e.event_id for e in dev}
        ho_ids = {e.event_id for e in ho}
        assert dev_ids.isdisjoint(ho_ids), f"Overlap: {dev_ids & ho_ids}"

    def test_split_covers_all(self, events: list[FailedTransactionEvent]) -> None:
        dev, ho = split_dataset(events, seed=42)
        all_ids = {e.event_id for e in events}
        split_ids = {e.event_id for e in dev} | {e.event_id for e in ho}
        assert all_ids == split_ids

    def test_approximately_80_20(self, events: list[FailedTransactionEvent]) -> None:
        dev, ho = split_dataset(events, seed=42)
        total = len(events)
        dev_frac = len(dev) / total
        assert 0.75 <= dev_frac <= 0.85, f"Dev fraction {dev_frac} not near 0.80"

    def test_split_size(self, events: list[FailedTransactionEvent]) -> None:
        dev, ho = split_dataset(events, seed=42)
        assert len(dev) + len(ho) == len(events)


# ===================================================================
# 5. Valid amounts, timestamps, and IDs
# ===================================================================


class TestFieldValues:
    """Generated values must be realistic and valid."""

    @pytest.fixture()
    def events(self) -> list[FailedTransactionEvent]:
        return generate_dataset(seed=42)

    def test_amounts_positive(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            assert event.amount > 0

    def test_amounts_in_range(self, events: list[FailedTransactionEvent]) -> None:
        """Amounts should be ₹50–₹25,000 (5000–2500000 paise)."""
        for event in events:
            assert 5000 <= event.amount <= 2500000

    def test_amounts_rounded(self, events: list[FailedTransactionEvent]) -> None:
        """Amounts should be rounded to nearest 100 paise."""
        for event in events:
            assert event.amount % 100 == 0

    def test_timestamps_valid(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            assert isinstance(event.timestamp, datetime)

    def test_timestamps_in_range(self, events: list[FailedTransactionEvent]) -> None:
        """All timestamps should be within the 5-day generation window."""
        start = datetime(2026, 8, 25, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 30, 0, 0, 0, tzinfo=timezone.utc)
        for event in events:
            ts = event.timestamp.replace(tzinfo=timezone.utc)
            assert start <= ts <= end, f"{event.event_id} timestamp {ts} out of range"

    def test_event_ids_unique(self, events: list[FailedTransactionEvent]) -> None:
        ids = [e.event_id for e in events]
        assert len(ids) == len(set(ids))

    def test_attempt_number_valid(self, events: list[FailedTransactionEvent]) -> None:
        for event in events:
            assert event.attempt_number >= 1

    def test_payment_method_valid(self, events: list[FailedTransactionEvent]) -> None:
        valid_methods = {pm.value for pm in PaymentMethod}
        for event in events:
            assert event.payment_method.value in valid_methods

    def test_transaction_type_valid(self, events: list[FailedTransactionEvent]) -> None:
        valid_types = {tt.value for tt in TransactionType}
        for event in events:
            assert event.type.value in valid_types

    def test_mandate_status_for_subscriptions(
        self, events: list[FailedTransactionEvent]
    ) -> None:
        """Subscription events should have a mandate_status."""
        subs = [e for e in events if e.type == TransactionType.SUBSCRIPTION]
        assert len(subs) > 0, "Expected some subscription events"
        for event in subs:
            assert event.mandate_status is not None

    def test_mandate_status_null_for_one_time(
        self, events: list[FailedTransactionEvent]
    ) -> None:
        """One-time events should have null mandate_status."""
        one_time = [e for e in events if e.type == TransactionType.ONE_TIME]
        assert len(one_time) > 0, "Expected some one-time events"
        for event in one_time:
            assert event.mandate_status is None


# ===================================================================
# 6. Dataset count
# ===================================================================


class TestDatasetSize:
    """Generated dataset must be in the 50-100 range."""

    def test_default_count(self) -> None:
        events = generate_dataset(seed=42)
        assert 50 <= len(events) <= 100

    def test_minimum_count(self) -> None:
        events = generate_dataset(seed=42, total=50)
        assert len(events) == 50

    def test_maximum_count(self) -> None:
        events = generate_dataset(seed=42, total=100)
        assert len(events) == 100

    def test_rejects_below_minimum(self) -> None:
        with pytest.raises(ValueError, match="must be between 50 and 100"):
            generate_dataset(seed=42, total=49)

    def test_rejects_above_maximum(self) -> None:
        with pytest.raises(ValueError, match="must be between 50 and 100"):
            generate_dataset(seed=42, total=101)


# ===================================================================
# 7. Repeated generation does not corrupt output
# ===================================================================


class TestIdempotentFileWrites:
    """Running the generator repeatedly must produce identical files."""

    def test_repeated_writes_identical(self, tmp_path: Path) -> None:
        """Write twice to the same directory; files must be identical."""
        stats_a = generate_and_write(seed=42, data_dir=tmp_path)
        content_a_dev = Path(stats_a["synthetic_path"]).read_text()
        content_a_ho = Path(stats_a["held_out_path"]).read_text()

        stats_b = generate_and_write(seed=42, data_dir=tmp_path)
        content_b_dev = Path(stats_b["synthetic_path"]).read_text()
        content_b_ho = Path(stats_b["held_out_path"]).read_text()

        assert content_a_dev == content_b_dev
        assert content_a_ho == content_b_ho

    def test_three_consecutive_writes(self, tmp_path: Path) -> None:
        """Three consecutive writes with the same seed produce the same data."""
        results = []
        for _ in range(3):
            stats = generate_and_write(seed=42, data_dir=tmp_path)
            dev = Path(stats["synthetic_path"]).read_text()
            ho = Path(stats["held_out_path"]).read_text()
            results.append((dev, ho))
        assert all(r == results[0] for r in results)

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        stats = generate_and_write(seed=42, data_dir=tmp_path)
        dev = json.loads(Path(stats["synthetic_path"]).read_text())
        ho = json.loads(Path(stats["held_out_path"]).read_text())
        assert isinstance(dev, list)
        assert isinstance(ho, list)
        assert len(dev) > 0
        assert len(ho) > 0

    def test_no_overlap_in_written_files(self, tmp_path: Path) -> None:
        stats = generate_and_write(seed=42, data_dir=tmp_path)
        dev = json.loads(Path(stats["synthetic_path"]).read_text())
        ho = json.loads(Path(stats["held_out_path"]).read_text())
        dev_ids = {r["event_id"] for r in dev}
        ho_ids = {r["event_id"] for r in ho}
        assert dev_ids.isdisjoint(ho_ids)


# ===================================================================
# 8. Stats correctness
# ===================================================================


class TestStats:
    """The summary statistics returned by generate_and_write must be accurate."""

    def test_stats_counts(self, tmp_path: Path) -> None:
        stats = generate_and_write(seed=42, data_dir=tmp_path)
        assert stats["total_events"] == TOTAL_EVENTS
        assert stats["dev_count"] + stats["held_out_count"] == stats["total_events"]
        assert 0.75 <= stats["dev_fraction"] <= 0.85

    def test_stats_category_counts_sum(self, tmp_path: Path) -> None:
        stats = generate_and_write(seed=42, data_dir=tmp_path)
        all_sum = sum(stats["category_counts_all"].values())
        dev_sum = sum(stats["category_counts_dev"].values())
        ho_sum = sum(stats["category_counts_held_out"].values())
        assert all_sum == stats["total_events"]
        assert dev_sum == stats["dev_count"]
        assert ho_sum == stats["held_out_count"]

    def test_all_categories_in_stats(self, tmp_path: Path) -> None:
        stats = generate_and_write(seed=42, data_dir=tmp_path)
        assert set(stats["category_counts_all"].keys()) == EXPECTED_CATEGORIES
