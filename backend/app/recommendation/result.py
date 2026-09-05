"""Structured output from the AI revenue-risk advisor."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.models.payment_event import FailureCategory
from app.policy.result import PolicyAction


class ApprovedPaymentHistory(BaseModel):
    """Redacted, aggregate payment history approved for model context.

    This intentionally contains no customer identifiers, payment IDs, raw
    descriptions, or credentials.  It gives the advisor enough context to
    distinguish a first failure from a repeated pattern without exposing the
    underlying audit rows.
    """

    prior_event_count: int = Field(default=0, ge=0)
    successful_payment_count: int = Field(default=0, ge=0)
    failed_payment_count: int = Field(default=0, ge=0)
    recovered_amount: int = Field(default=0, ge=0)
    prior_recovery_attempts: int = Field(default=0, ge=0)
    last_outcome: str | None = None
    last_failure_category: str | None = None
    recent_outcomes: list[str] = Field(default_factory=list, max_length=5)


class RecommendationFallbackReason(str, Enum):
    """Why the advisor did not return a live model recommendation."""

    API_KEY_UNAVAILABLE = "api_key_unavailable"
    NIM_TIMEOUT = "nim_timeout"
    MODEL_UNAVAILABLE = "model_unavailable"
    NETWORK_FAILURE = "network_failure"
    INVALID_JSON_RESPONSE = "invalid_json_response"
    INVALID_MODEL_RESPONSE = "invalid_model_response"
    PIPELINE_FAILURE = "pipeline_failure"


class RecoveryRecommendation(BaseModel):
    """Advisory model output, never an authorization object.

    The model intentionally has no retry count, amount, cooldown, or
    ``authorized`` field. Those values belong to deterministic policy.
    """

    success: bool = Field(..., description="Whether NIM returned valid output")
    revenue_at_risk: bool = Field(
        ..., description="Whether the supplied event represents at-risk revenue"
    )
    risk_score: float = Field(..., ge=0.0, le=1.0)
    suggested_cause: FailureCategory | None = Field(default=None)
    suggested_action: PolicyAction | None = Field(default=None)
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list, max_length=5)
    model_id: str = Field(..., description="Model or fallback provider reference")
    is_fallback: bool = Field(default=False)
    fallback_reason: RecommendationFallbackReason | None = Field(default=None)
    error: str | None = Field(default=None)
    prompt_version: str | None = Field(default=None)
    latency_ms: int | None = Field(default=None, ge=0)
