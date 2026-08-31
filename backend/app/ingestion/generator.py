"""Deterministic synthetic failed-payment dataset generator.

Generates 50-100 synthetic failed payment transactions across all
failure categories defined in the taxonomy. The generator is fully
deterministic — running it twice with the same seed produces the
exact same dataset.

Usage:
    python -m app.ingestion.generator          # default seed=42
    python -m app.ingestion.generator --seed 7 # custom seed
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    MandateStatus,
    PaymentMethod,
    TransactionType,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SEED = 42
TOTAL_EVENTS = 80  # target count (within 50–100 range)
DEV_FRACTION = 0.80  # 80% development, 20% held-out

# Base timestamp: 2026-08-25 00:00:00 UTC
_BASE_TS = datetime(2026, 8, 25, 0, 0, 0, tzinfo=timezone.utc)

# Merchant pool (synthetic)
_MERCHANTS = [f"merch_synth_{i:03d}" for i in range(1, 6)]

# Customer pool (synthetic)
_CUSTOMERS = [f"cust_synth_{i:04d}" for i in range(1, 31)]

# ---------------------------------------------------------------------------
# Failure-category templates
# ---------------------------------------------------------------------------

# Each template defines the fields that vary by failure category.
# Weights control the relative frequency in the dataset.
_FAILURE_TEMPLATES: list[dict[str, Any]] = [
    # --- Insufficient Funds (~25%) ---
    {
        "category": FailureCategory.INSUFFICIENT_FUNDS,
        "error_code": "BAD_REQUEST_ERROR",
        "error_descriptions": [
            "Your payment could not be completed due to insufficient account balance",
            "Insufficient funds in the account to complete this transaction",
            "Transaction declined: insufficient balance",
        ],
        "weight": 25,
        "payment_methods": [PaymentMethod.CARD, PaymentMethod.UPI, PaymentMethod.NETBANKING],
    },
    # --- Expired Card (~15%) ---
    {
        "category": FailureCategory.EXPIRED_CARD,
        "error_code": "BAD_REQUEST_ERROR",
        "error_descriptions": [
            "The card has expired. Please use a different card",
            "Card expired — unable to process payment",
            "Transaction declined: card expiry date has passed",
        ],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD],
    },
    # --- Network Error (~15%) ---
    {
        "category": FailureCategory.NETWORK_ERROR,
        "error_code": "GATEWAY_ERROR",
        "error_descriptions": [
            "Payment processing failed due to a gateway timeout",
            "Network error: bank gateway did not respond in time",
            "Gateway connection timed out while processing payment",
        ],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD, PaymentMethod.UPI, PaymentMethod.NETBANKING],
    },
    # --- Bank Decline (~15%) ---
    {
        "category": FailureCategory.BANK_DECLINE,
        "error_code": "BAD_REQUEST_ERROR",
        "error_descriptions": [
            "The card issuing bank declined the transaction",
            "Payment declined by the issuing bank",
            "Transaction not permitted by the card issuer",
        ],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD],
    },
    # --- Authentication Failure (~15%) ---
    {
        "category": FailureCategory.AUTHENTICATION_FAILURE,
        "error_code": "BAD_REQUEST_ERROR",
        "error_descriptions": [
            "3D Secure authentication failed or was not completed",
            "OTP verification failed — payment could not be authenticated",
            "Authentication failed: customer did not complete 3DS challenge",
        ],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD, PaymentMethod.NETBANKING],
    },
    # --- Unknown (~15%) ---
    {
        "category": FailureCategory.UNKNOWN,
        "error_code": "SERVER_ERROR",
        "error_descriptions": [
            "An unexpected error occurred while processing the payment",
            "Internal processing error — please retry later",
            "Unknown failure: payment could not be completed",
        ],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD, PaymentMethod.UPI, PaymentMethod.NETBANKING],
    },
]


# ---------------------------------------------------------------------------
# Generator core
# ---------------------------------------------------------------------------


def _build_category_counts(rng: random.Random, total: int) -> list[tuple[dict[str, Any], int]]:
    """Distribute *total* events across failure categories by weight.

    The distribution is deterministic given the same *rng* state.
    """
    total_weight = sum(t["weight"] for t in _FAILURE_TEMPLATES)
    # Base allocation by proportion, then distribute remainder randomly
    counts: list[int] = []
    allocated = 0
    for tmpl in _FAILURE_TEMPLATES:
        base = int(total * tmpl["weight"] / total_weight)
        counts.append(base)
        allocated += base

    remainder = total - allocated
    indices = list(range(len(_FAILURE_TEMPLATES)))
    for _ in range(remainder):
        idx = rng.choice(indices)
        counts[idx] += 1

    return list(zip(_FAILURE_TEMPLATES, counts))


def generate_dataset(
    seed: int = DEFAULT_SEED,
    total: int = TOTAL_EVENTS,
) -> list[FailedTransactionEvent]:
    """Generate a deterministic synthetic failed-payment dataset.

    Args:
        seed: Random seed for reproducibility.
        total: Total number of events to generate (50-100).

    Returns:
        A list of FailedTransactionEvent objects, sorted by event_id.
    """
    if not (50 <= total <= 100):
        raise ValueError(f"total must be between 50 and 100, got {total}")

    rng = random.Random(seed)
    category_counts = _build_category_counts(rng, total)

    events: list[FailedTransactionEvent] = []
    event_idx = 0

    for tmpl, count in category_counts:
        for _ in range(count):
            event_idx += 1
            event_id = f"evt_synth_{event_idx:04d}"
            payment_id = f"pay_test_{rng.randint(100000, 999999)}"
            merchant = rng.choice(_MERCHANTS)
            customer = rng.choice(_CUSTOMERS)

            # Transaction type: ~40% subscription, 60% one-time
            tx_type = (
                TransactionType.SUBSCRIPTION
                if rng.random() < 0.4
                else TransactionType.ONE_TIME
            )

            # Mandate status only for subscriptions
            mandate_status = None
            if tx_type == TransactionType.SUBSCRIPTION:
                if tmpl["category"] == FailureCategory.EXPIRED_CARD:
                    mandate_status = rng.choice(
                        [MandateStatus.EXPIRED, MandateStatus.PAUSED]
                    )
                else:
                    mandate_status = rng.choice(
                        [MandateStatus.ACTIVE, MandateStatus.EXPIRED, MandateStatus.PAUSED]
                    )

            # Amount: realistic INR range (₹50 – ₹25,000 in paise)
            amount = rng.randint(5000, 2500000)
            # Round to nearest 100 paise for realism
            amount = (amount // 100) * 100

            # Payment method: pick from category-allowed methods
            payment_method = rng.choice(tmpl["payment_methods"])

            # Error description
            error_description = rng.choice(tmpl["error_descriptions"])

            # Attempt number: mostly 1, some retries
            attempt_weights = [0.6, 0.25, 0.1, 0.05]
            attempt_number = rng.choices([1, 2, 3, 4], weights=attempt_weights, k=1)[0]

            # Timestamp: spread across a 5-day window from base
            offset_seconds = rng.randint(0, 5 * 24 * 3600)
            timestamp = _BASE_TS + timedelta(seconds=offset_seconds)

            events.append(
                FailedTransactionEvent(
                    event_id=event_id,
                    razorpay_payment_id=payment_id,
                    merchant_id=merchant,
                    customer_id=customer,
                    type=tx_type,
                    amount=amount,
                    currency="INR",
                    payment_method=payment_method,
                    error_code=tmpl["error_code"],
                    error_description=error_description,
                    failure_category=tmpl["category"],
                    attempt_number=attempt_number,
                    mandate_status=mandate_status,
                    timestamp=timestamp,
                )
            )

    # Sort by event_id for stable output
    events.sort(key=lambda e: e.event_id)
    return events


def split_dataset(
    events: list[FailedTransactionEvent],
    seed: int = DEFAULT_SEED,
    dev_fraction: float = DEV_FRACTION,
) -> tuple[list[FailedTransactionEvent], list[FailedTransactionEvent]]:
    """Deterministically split events into development and held-out sets.

    The split is based on a seeded shuffle so it is reproducible.
    No event appears in both sets.

    Args:
        events: Full list of events.
        seed: Random seed for the split shuffle.
        dev_fraction: Fraction of events for the development set.

    Returns:
        (dev_events, held_out_events)
    """
    rng = random.Random(seed)
    indices = list(range(len(events)))
    rng.shuffle(indices)

    split_point = int(len(events) * dev_fraction)
    dev_indices = sorted(indices[:split_point])
    held_out_indices = sorted(indices[split_point:])

    dev_events = [events[i] for i in dev_indices]
    held_out_events = [events[i] for i in held_out_indices]
    return dev_events, held_out_events


def events_to_dicts(events: list[FailedTransactionEvent]) -> list[dict[str, Any]]:
    """Serialize events to JSON-safe dictionaries."""
    return [
        json.loads(event.model_dump_json())
        for event in events
    ]


def write_dataset(
    events: list[FailedTransactionEvent],
    output_path: Path,
) -> Path:
    """Write a list of events to a JSON file.

    Args:
        events: Events to write.
        output_path: Target file path.

    Returns:
        The path written to.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = events_to_dicts(events)
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path


def generate_and_write(
    seed: int = DEFAULT_SEED,
    total: int = TOTAL_EVENTS,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate, split, and write datasets. Returns summary stats.

    Args:
        seed: Random seed.
        total: Total number of events.
        data_dir: Root data directory (defaults to <project>/data/).

    Returns:
        Dictionary with generation statistics.
    """
    if data_dir is None:
        # Resolve relative to project root (two levels up from this file)
        data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data"

    events = generate_dataset(seed=seed, total=total)
    dev_events, held_out_events = split_dataset(events, seed=seed)

    synthetic_path = data_dir / "synthetic" / "failed_transactions.json"
    held_out_path = data_dir / "held_out" / "failed_transactions.json"

    write_dataset(dev_events, synthetic_path)
    write_dataset(held_out_events, held_out_path)

    # Compute category counts
    from collections import Counter

    dev_categories = Counter(e.failure_category.value for e in dev_events)
    held_out_categories = Counter(e.failure_category.value for e in held_out_events)
    all_categories = Counter(e.failure_category.value for e in events)

    return {
        "seed": seed,
        "total_events": len(events),
        "dev_count": len(dev_events),
        "held_out_count": len(held_out_events),
        "dev_fraction": len(dev_events) / len(events),
        "synthetic_path": str(synthetic_path),
        "held_out_path": str(held_out_path),
        "category_counts_all": dict(all_categories),
        "category_counts_dev": dict(dev_categories),
        "category_counts_held_out": dict(held_out_categories),
    }
