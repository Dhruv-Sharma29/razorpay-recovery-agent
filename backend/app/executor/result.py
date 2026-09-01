"""Structured output of the recovery executor.

The execution result records what the executor attempted and the outcome.
It does not carry authority — only the policy engine can authorize actions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """Outcome status of an execution attempt."""

    SUCCESS = "success"
    """The authorized action was executed successfully."""

    REJECTED = "rejected"
    """The executor refused to execute because the policy did not authorize it."""

    FAILED = "failed"
    """The execution was attempted but failed (e.g. downstream error)."""

    DUPLICATE = "duplicate"
    """The action was already executed (idempotency guard)."""

    INVALID = "invalid"
    """The input was malformed or the policy decision was not recognized."""


class ExecutionResult(BaseModel):
    """Structured result of a recovery execution attempt.

    Returned by ``RecoveryExecutor.execute``.  Always safe to use —
    even when execution fails the result is a structured record, not
    an exception.
    """

    status: ExecutionStatus = Field(
        ...,
        description="Overall outcome of the execution attempt",
    )

    action_attempted: str = Field(
        ...,
        description=(
            "The policy action that was attempted or would have been "
            "attempted (e.g. 'scheduled_retry', 'escalate')"
        ),
    )

    payment_id: str = Field(
        ...,
        description="The Razorpay payment ID from the payment event",
    )

    event_id: str = Field(
        ...,
        description="The event ID from the payment event",
    )

    executed: bool = Field(
        ...,
        description=(
            "Whether the recovery action was actually executed. "
            "False for rejected, duplicate, and invalid statuses."
        ),
    )

    execution_id: str | None = Field(
        default=None,
        description=(
            "Unique identifier for this execution, if one was generated. "
            "None when the action was not executed."
        ),
    )

    idempotency_key: str = Field(
        ...,
        description=(
            "Deterministic key derived from the payment context "
            "to prevent duplicate execution."
        ),
    )

    error: str | None = Field(
        default=None,
        description="Error message if execution failed or was rejected. None on success.",
    )

    reason: str = Field(
        ...,
        description="Human-readable explanation of the execution outcome.",
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the execution attempt.",
    )
