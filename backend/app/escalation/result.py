"""Structured output of the deterministic escalation handler.

Escalation records that a payment cannot be recovered automatically.
It does not authorize recovery, execute payments, or change policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class EscalationStatus(str, Enum):
    """Whether human intervention is required."""

    OPEN = "open"
    """Escalation opened; automatic recovery must not proceed."""

    NOT_REQUIRED = "not_required"
    """No escalation is needed for this context."""

    FAILED_CLOSED = "failed_closed"
    """Input was missing or malformed; fail closed (no recovery)."""


class EscalationTrigger(str, Enum):
    """Why the escalation handler was invoked."""

    POLICY_DENIAL = "policy_denial"
    MANUAL_REVIEW = "manual_review"
    UNKNOWN_UNSAFE = "unknown_unsafe"
    EXECUTOR_FAILURE = "executor_failure"
    REASONING_FAILURE = "reasoning_failure"
    MALFORMED_INPUT = "malformed_input"


class EscalationSeverity(str, Enum):
    """Priority for human follow-up."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EscalationResult(BaseModel):
    """Structured escalation decision.

    ``authorizes_recovery`` is always False. The escalation component
    never grants recovery authority.
    """

    status: EscalationStatus = Field(
        ...,
        description="Escalation status (open, not_required, or failed_closed)",
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of the escalation outcome",
    )
    payment_id: str = Field(
        ...,
        description="Razorpay payment / transaction ID",
    )
    event_id: str = Field(
        ...,
        description="Ingestion event identifier",
    )
    severity: EscalationSeverity = Field(
        ...,
        description="Priority for human intervention",
    )
    recommended_next_step: str = Field(
        ...,
        description="Deterministic next step for a human operator",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the escalation evaluation",
    )
    trigger: EscalationTrigger | None = Field(
        default=None,
        description="Primary escalation trigger, if any",
    )
    escalation_id: str = Field(
        ...,
        description="Deterministic identifier for this escalation evaluation",
    )
    authorizes_recovery: Literal[False] = Field(
        default=False,
        description="Always False. Escalation never authorizes recovery.",
    )
    policy_action: str | None = Field(
        default=None,
        description="Snapshot of the policy action; never mutated",
    )
    policy_reason: str | None = Field(
        default=None,
        description="Snapshot of the policy reason; never mutated",
    )
