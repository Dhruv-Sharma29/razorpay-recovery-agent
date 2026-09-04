"""Structured output of the deterministic recovery policy engine."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.models.payment_event import FailureCategory


class PolicyAction(str, Enum):
    """Actions the policy engine can prescribe.

    Matches the decision policy table in Section 4.3 of the
    implementation plan.
    """

    SCHEDULED_RETRY = "scheduled_retry"
    IMMEDIATE_RETRY = "immediate_retry"
    TRIGGER_REAUTHORIZATION = "trigger_reauthorization"
    SWITCH_PAYMENT_METHOD = "switch_payment_method"
    RESEND_AUTH_PROMPT = "resend_auth_prompt"
    ESCALATE = "escalate"
    NO_ACTION = "no_action"


class EscalationReason(str, Enum):
    """Why a transaction was escalated instead of auto-recovered."""

    AMOUNT_EXCEEDS_LIMIT = "amount_exceeds_limit"
    RETRY_LIMIT_EXHAUSTED = "retry_limit_exhausted"
    GLOBAL_ATTEMPT_CAP = "global_attempt_cap"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"
    UNKNOWN_FAILURE = "unknown_failure"
    INVALID_CLASSIFICATION = "invalid_classification"
    MISSING_CLASSIFICATION = "missing_classification"
    UNSAFE_CONDITION = "unsafe_condition"


class PolicyDecision(BaseModel):
    """Result of a recovery policy evaluation.

    The policy engine determines WHAT the system is allowed to do.
    It does NOT execute the action.
    """

    action: PolicyAction = Field(
        ...,
        description="The prescribed recovery action",
    )
    automatic_recovery_allowed: bool = Field(
        ...,
        description="Whether the system may execute the action automatically",
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of the policy decision",
    )
    rule_id: str = Field(
        ...,
        description="Stable identifier of the policy rule that fired",
    )
    failure_category: FailureCategory = Field(
        ...,
        description="The classified failure category that drove the decision",
    )
    escalation_required: bool = Field(
        ...,
        description="Whether manual review / escalation is required",
    )
    escalation_reason: EscalationReason | None = Field(
        default=None,
        description="Reason for escalation, if applicable",
    )
    max_retries_for_category: int = Field(
        ...,
        description="Maximum retries permitted by policy for this failure category",
    )
    current_attempt: int = Field(
        ...,
        description="Current attempt number from the payment event",
    )
    amount: int = Field(
        ...,
        description="Transaction amount in paise",
    )
    cooldown_seconds: int = Field(
        default=0,
        description=(
            "Seconds the authorized action must wait before running. "
            "0 means execute immediately."
        ),
    )

    amount_limit: int = Field(
        ...,
        description="Configured automatic recovery amount limit in paise",
    )
