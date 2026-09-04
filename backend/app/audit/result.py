"""Structured audit records and write results.

Audit is a recording layer only. It never authorizes recovery,
executes payments, or mutates policy decisions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class AuditOutcome(str, Enum):
    """Final recorded outcome for a recovery-agent decision."""

    RECOVERED = "recovered"
    PENDING = "pending"
    DENIED = "denied"
    ESCALATED = "escalated"
    EXECUTION_FAILED = "execution_failed"
    RECORDED = "recorded"
    AUDIT_FAILED = "audit_failed"


class AuditRecord(BaseModel):
    """One append-only audit event.

    Identifiers are operational (event/payment IDs). Customer PII and
    credentials are not stored.
    """

    audit_id: str = Field(..., description="Unique id for this audit row")
    event_id: str = Field(..., description="Payment event identifier")
    payment_id: str = Field(..., description="Razorpay payment / transaction ID")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the record was written",
    )
    classification_category: str | None = Field(
        default=None,
        description="Failure classification category, if available",
    )
    classification_reason: str | None = Field(
        default=None,
        description="Classifier reason, if available",
    )
    policy_action: str | None = Field(
        default=None,
        description="Policy action snapshot",
    )
    policy_reason: str | None = Field(
        default=None,
        description="Policy reason snapshot",
    )
    automatic_recovery_allowed: bool | None = Field(
        default=None,
        description="Snapshot of the policy authorization flag (not granted by audit)",
    )
    reasoning_success: bool | None = Field(
        default=None,
        description="Whether the reasoning layer succeeded",
    )
    reasoning_reference: str | None = Field(
        default=None,
        description="Reasoning model id or 'fallback' reference",
    )
    execution_status: str | None = Field(
        default=None,
        description="Executor status, if execution ran",
    )
    execution_reference: str | None = Field(
        default=None,
        description="Executor execution_id, if any",
    )
    escalation_status: str | None = Field(
        default=None,
        description="Escalation status, if evaluated",
    )
    escalation_reference: str | None = Field(
        default=None,
        description="Escalation id, if any",
    )
    final_outcome: AuditOutcome = Field(
        ...,
        description="Derived outcome for this audit event",
    )
    error: str | None = Field(
        default=None,
        description="Error information, if any (never includes secrets)",
    )
    attempt_number: int | None = Field(
        default=None,
        description="Attempt number from the payment event",
    )
    amount: int | None = Field(
        default=None,
        description="Transaction amount in paise",
    )

    # --- Risk dimensions ------------------------------------------------
    # Carried from the payment event so revenue-at-risk can be aggregated
    # from the audit log alone, without re-running the pipeline.

    merchant_id: str | None = Field(
        default=None, description="Merchant the failed payment belongs to"
    )
    customer_ref: str | None = Field(
        default=None,
        description=(
            "Pseudonymous, stable reference for the customer. Lets repeat "
            "failures be counted without storing the customer id, which is "
            "PII and is deliberately never persisted."
        ),
    )
    transaction_type: str | None = Field(
        default=None, description="one_time or subscription"
    )
    mandate_status: str | None = Field(
        default=None,
        description="Mandate state for subscriptions: active / expired / paused",
    )

    # --- Decision chain -------------------------------------------------
    # cause -> rule -> bound -> action -> outcome -> recovered amount.
    # Every value below already existed on the result objects; persisting
    # them makes each record independently auditable without replaying the
    # pipeline.

    classification_rule_id: str | None = Field(
        default=None,
        description="Which classifier rule matched the failure (the cause)",
    )
    policy_rule_id: str | None = Field(
        default=None,
        description="Which policy rule decided the action",
    )
    amount_limit: int | None = Field(
        default=None,
        description="Auto-recovery amount ceiling applied, in paise (the bound)",
    )
    max_retries: int | None = Field(
        default=None,
        description="Retry ceiling for this category (the bound)",
    )
    cooldown_seconds: int | None = Field(
        default=None,
        description="Cooldown the policy required before acting",
    )
    scheduled_for: str | None = Field(
        default=None,
        description="ISO timestamp a deferred retry became eligible, if scheduled",
    )
    payment_status: str | None = Field(
        default=None,
        description="Simulated gateway status: captured / failed / not_attempted",
    )
    amount_recovered: int | None = Field(
        default=None,
        description="Money actually recovered for this record, in paise",
    )
    escalation_trigger: str | None = Field(
        default=None,
        description="Why escalation fired, when it did",
    )
    reasoning_is_fallback: bool | None = Field(
        default=None,
        description=(
            "True when the explanation came from the deterministic fallback "
            "rather than the model"
        ),
    )


class AuditWriteResult(BaseModel):
    """Result of an audit write attempt.

    ``authorizes_recovery`` is always False. A failed write must never
    be treated as recovery authorization.
    """

    recorded: bool = Field(..., description="Whether the row was appended")
    audit_id: str | None = Field(
        default=None,
        description="Id of the appended row, if recorded",
    )
    error: str | None = Field(
        default=None,
        description="Write error, if the append failed",
    )
    authorizes_recovery: Literal[False] = Field(
        default=False,
        description="Always False. Audit never authorizes recovery.",
    )
    record: AuditRecord | None = Field(
        default=None,
        description="The appended record, if recorded",
    )
