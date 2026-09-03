"""Pydantic models for failed payment transaction events.

Schema matches Section 4.1 of the implementation plan.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FailureCategory(str, Enum):
    """Root-cause categories from the failure taxonomy (Section 4.2)."""

    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_CARD = "expired_card"
    NETWORK_ERROR = "network_error"
    BANK_DECLINE = "bank_decline"
    AUTHENTICATION_FAILURE = "authentication_failure"
    UNKNOWN = "unknown"


class PaymentMethod(str, Enum):
    """Supported payment methods."""

    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"


class TransactionType(str, Enum):
    """Payment transaction type."""

    ONE_TIME = "one_time"
    SUBSCRIPTION = "subscription"


class MandateStatus(str, Enum):
    """Mandate status for subscription payments."""

    ACTIVE = "active"
    EXPIRED = "expired"
    PAUSED = "paused"


class FailedTransactionEvent(BaseModel):
    """A single failed payment transaction event.

    Matches the input schema defined in Section 4.1 of the
    recovery-agent-implementation-plan.
    """

    event_id: str = Field(..., description="Unique event identifier, e.g. evt_001")
    razorpay_payment_id: str = Field(
        ..., description="Razorpay payment ID, e.g. pay_test_xxx"
    )
    merchant_id: str = Field(..., description="Merchant identifier")
    customer_id: str = Field(..., description="Customer identifier")
    type: TransactionType = Field(
        ..., description="subscription or one_time"
    )
    amount: int = Field(
        ..., ge=100, description="Amount in smallest currency unit (paise)"
    )
    currency: str = Field(default="INR", description="ISO 4217 currency code")
    payment_method: PaymentMethod = Field(
        ..., description="upi, card, or netbanking"
    )
    error_code: str = Field(
        ..., description="Razorpay-style error code"
    )
    error_description: str = Field(
        ..., description="Human-readable failure description"
    )
    failure_category: FailureCategory = Field(
        ..., description="Classified root-cause category"
    )
    attempt_number: int = Field(
        ..., ge=1, description="Which attempt this is (1-indexed)"
    )
    mandate_status: Optional[MandateStatus] = Field(
        default=None,
        description="Mandate status for subscription payments, null for one_time",
    )
    timestamp: datetime = Field(
        ..., description="ISO 8601 timestamp of the event"
    )
