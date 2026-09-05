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

# Anti-circularity note
# ---------------------
# Classification is driven by the structured Razorpay-style ``error_code`` —
# exactly as a production integration works, where the provider returns a code
# and the classifier maps it. Descriptions are deliberately NOT copies of the
# classifier's message-match tokens, so evaluation accuracy reflects the
# code->category mapping and precedence rules rather than the dataset echoing
# the classifier's own keywords back at it. The held-out slice is additionally
# re-worded from a disjoint phrase pool (see ``_HELD_OUT_DESCRIPTIONS``), so the
# held-out prose is genuinely unseen text.
#
# Each template defines the fields that vary by failure category. ``weight``
# controls the relative frequency in the dataset. ``error_codes`` is the pool of
# realistic codes for that category (the classifier recognizes each of them).
_FAILURE_TEMPLATES: list[dict[str, Any]] = [
    # --- Insufficient Funds (~25%) ---
    {
        "category": FailureCategory.INSUFFICIENT_FUNDS,
        "error_codes": ["INSUFFICIENT_FUNDS"],
        "weight": 25,
        "payment_methods": [PaymentMethod.CARD, PaymentMethod.UPI, PaymentMethod.NETBANKING],
    },
    # --- Expired Card (~15%) ---
    {
        "category": FailureCategory.EXPIRED_CARD,
        # MANDATE_EXPIRED is substituted for subscription events at generation
        # time (see generate_dataset).
        "error_codes": ["EXPIRED_CARD", "CARD_EXPIRED"],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD],
    },
    # --- Network Error (~15%) ---
    {
        "category": FailureCategory.NETWORK_ERROR,
        "error_codes": ["GATEWAY_ERROR", "GATEWAY_TIMEOUT", "NETWORK_ERROR"],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD, PaymentMethod.UPI, PaymentMethod.NETBANKING],
    },
    # --- Bank Decline (~15%) ---
    {
        "category": FailureCategory.BANK_DECLINE,
        "error_codes": ["BANK_DECLINED", "CARD_DECLINED", "ISSUER_DECLINED"],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD],
    },
    # --- Authentication Failure (~15%) ---
    {
        "category": FailureCategory.AUTHENTICATION_FAILURE,
        "error_codes": ["AUTHENTICATION_ERROR", "AUTHENTICATION_FAILED"],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD, PaymentMethod.NETBANKING],
    },
    # --- Overdue Receivable (~10%) ---
    # B2B invoices that were never a gateway failure at all: the money is
    # simply late. Included because a batch that only contains gateway errors
    # cannot demonstrate receivables recovery, and the policy for it — a
    # 72-hour chaser rather than a retry — is one of the more distinctive
    # decisions the engine makes.
    {
        "category": FailureCategory.OVERDUE_RECEIVABLE,
        "error_codes": ["INVOICE_OVERDUE", "PAYMENT_OVERDUE"],
        "weight": 10,
        "payment_methods": [PaymentMethod.NETBANKING, PaymentMethod.UPI],
    },
    # --- Unknown (~15%) ---
    {
        # Generic codes with descriptions that carry no recognizable signal, so
        # the classifier correctly fails closed to ``unknown``.
        "category": FailureCategory.UNKNOWN,
        "error_codes": ["SERVER_ERROR", "BAD_REQUEST_ERROR"],
        "weight": 15,
        "payment_methods": [PaymentMethod.CARD, PaymentMethod.UPI, PaymentMethod.NETBANKING],
    },
]

# Development descriptions: realistic, varied, and intentionally free of the
# classifier's exact message-match phrases (the error_code carries the signal).
_DEV_DESCRIPTIONS: dict[FailureCategory, list[str]] = {
    FailureCategory.INSUFFICIENT_FUNDS: [
        "Customer's account did not hold enough balance at capture time",
        "The bank returned a low-balance response for this debit",
        "Debit rejected — available money was below the charged amount",
        "Charge could not be covered by the account at the time of payment",
    ],
    FailureCategory.EXPIRED_CARD: [
        "The saved card is past its validity date",
        "Stored card is no longer active for payments",
        "The card on file lapsed before this charge",
        "Recurring instrument could not be billed because the card aged out",
    ],
    FailureCategory.NETWORK_ERROR: [
        "Upstream processor did not answer before the deadline",
        "The acquiring bank endpoint stopped responding mid-request",
        "Connection to the processor dropped during capture",
        "The switch failed to complete the round-trip in time",
    ],
    FailureCategory.BANK_DECLINE: [
        "Issuer refused to authorize this charge",
        "The customer's bank turned down the transaction",
        "Charge blocked by the card-issuing institution",
        "Authorization was not granted by the issuer",
    ],
    FailureCategory.OVERDUE_RECEIVABLE: [
        "Invoice is past its due date and remains unpaid",
        "Payment for this invoice is overdue",
        "Receivable outstanding beyond agreed terms",
    ],
    FailureCategory.AUTHENTICATION_FAILURE: [
        "Cardholder did not finish the extra verification step",
        "The additional security check was not cleared",
        "Step-up verification was abandoned before completion",
        "The customer left the challenge prompt unfinished",
    ],
    FailureCategory.UNKNOWN: [
        "The processor returned a response we could not map to a known cause",
        "Payment did not complete for a reason the provider left unspecified",
        "An unclassified processing fault interrupted the charge",
        "Capture ended under conditions the system could not label",
    ],
}

# Held-out descriptions: a disjoint phrase pool. The held-out slice is re-worded
# from this at write time so its prose never appears in the development set.
_HELD_OUT_DESCRIPTIONS: dict[FailureCategory, list[str]] = {
    FailureCategory.OVERDUE_RECEIVABLE: [
        "Settlement for this invoice has not arrived by the agreed date",
        "Amount remains outstanding well past the payment terms",
        "Buyer has not settled this invoice within the credit period",
    ],
    FailureCategory.INSUFFICIENT_FUNDS: [
        "Payment stopped because the account balance fell short",
        "Issuer reported the account could not cover the amount",
        "Not enough spendable balance was available to settle the charge",
    ],
    FailureCategory.EXPIRED_CARD: [
        "Card on record has reached the end of its life and cannot be billed",
        "The linked card is no longer valid for this recurring charge",
        "Payment instrument is out of date and was not chargeable",
    ],
    FailureCategory.NETWORK_ERROR: [
        "Processor link went quiet and the request never finished",
        "The connection to the issuer lapsed before a reply arrived",
        "No response came back from the acquiring side in time",
    ],
    FailureCategory.BANK_DECLINE: [
        "Authorizing bank would not approve the payment",
        "The issuing side rejected this debit outright",
        "Approval was withheld by the customer's bank",
    ],
    FailureCategory.AUTHENTICATION_FAILURE: [
        "Customer never cleared the secondary verification",
        "The added identity check went uncompleted",
        "Verification lapsed before the cardholder confirmed",
    ],
    FailureCategory.UNKNOWN: [
        "The provider ended the payment without a categorizable reason",
        "Settlement aborted under conditions the system could not classify",
        "An unmapped fault stopped the charge from completing",
    ],
}


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

            # Error code: the realistic, structured classification signal.
            # Subscriptions whose card lapsed surface as a mandate error.
            if (
                tmpl["category"] == FailureCategory.EXPIRED_CARD
                and tx_type == TransactionType.SUBSCRIPTION
            ):
                error_code = "MANDATE_EXPIRED"
            else:
                error_code = rng.choice(tmpl["error_codes"])

            # Error description (development pool; deliberately not the
            # classifier's message tokens).
            error_description = rng.choice(_DEV_DESCRIPTIONS[tmpl["category"]])

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
                    error_code=error_code,
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

    # Re-word the held-out slice from a disjoint phrase pool so its prose is
    # genuinely unseen. Classification is code-driven, so the correct label is
    # unchanged — this only removes any chance of description leakage inflating
    # held-out accuracy. Deterministic: seeded and iterated in a fixed order.
    swap_rng = random.Random(seed + 1)
    held_out_events = [
        e.model_copy(
            update={
                "error_description": swap_rng.choice(
                    _HELD_OUT_DESCRIPTIONS[e.failure_category]
                )
            }
        )
        for e in held_out_events
    ]

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


if __name__ == "__main__":  # pragma: no cover
    # Delegate to the package CLI so the documented command
    # `python -m app.ingestion.generator [--seed] [--total]` works.
    import app.ingestion.__main__ as _cli

    _cli.main()
