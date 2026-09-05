"""Structured output of the end-to-end recovery pipeline.

Captures the full lifecycle of a recovery evaluation and its outcome.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.audit.result import AuditOutcome, AuditWriteResult
from app.classifier.result import ClassificationResult
from app.escalation.result import EscalationResult
from app.executor.result import ExecutionResult
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyDecision
from app.recommendation.result import RecoveryRecommendation
from app.reasoning.result import ReasoningResult


class PipelineResult(BaseModel):
    """The complete context and outcome of a recovery pipeline run."""

    payment_id: str = Field(
        ...,
        description="The Razorpay payment ID processed",
    )
    event_id: str = Field(
        ...,
        description="The unique event ID processed",
    )
    
    classification: ClassificationResult | None = Field(
        default=None,
        description="Result from the failure classifier",
    )
    recommendation: RecoveryRecommendation | None = Field(
        default=None,
        description="AI risk and intervention recommendation, if requested",
    )
    policy_decision: PolicyDecision | None = Field(
        default=None,
        description="Authoritative decision from the deterministic policy engine",
    )
    reasoning: ReasoningResult | None = Field(
        default=None,
        description="LLM explanation and context analysis",
    )
    execution: ExecutionResult | None = Field(
        default=None,
        description="Result from the recovery executor, if execution was authorized",
    )
    escalation: EscalationResult | None = Field(
        default=None,
        description="Result from the escalation handler",
    )
    audit_write: AuditWriteResult | None = Field(
        default=None,
        description="Result of the audit log append operation",
    )
    
    final_outcome: AuditOutcome = Field(
        ...,
        description="The final state of the transaction after processing",
    )
    
    error: str | None = Field(
        default=None,
        description="Pipeline-level error information, if any",
    )
    
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the pipeline completed",
    )
