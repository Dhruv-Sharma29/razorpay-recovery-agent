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
    OVERDUE_RECEIVABLE = "overdue_receivable"
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


class RazorpayContext(BaseModel):
    """Identifiers a real Razorpay recovery call needs, when they exist.

    Entirely optional. Synthetic batches carry none of this, and the
    simulated executor never looks at it — it exists so the live executor
    can tell "this customer has a mandate we may charge" apart from "this
    customer must be asked to pay again", rather than guessing.
    """

    customer_id: Optional[str] = Field(
        default=None,
        description="Razorpay customer handle (cust_...), not the merchant's own id.",
    )
    token_id: Optional[str] = Field(
        default=None,
        description=(
            "Saved card/mandate token from a prior authorised payment. Without "
            "one there is no lawful way to charge the customer unattended."
        ),
    )
    email: Optional[str] = Field(
        default=None, description="Where a payment link may be sent."
    )
    contact: Optional[str] = Field(
        default=None, description="Phone number a payment link may be sent to."
    )


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
    razorpay: Optional[RazorpayContext] = Field(
        default=None,
        description=(
            "Live-gateway identifiers. Absent for synthetic events, which is "
            "why the live executor must refuse rather than improvise."
        ),
    )

    timestamp: datetime = Field(
        ..., description="ISO 8601 timestamp of the event"
    )
