"""Dashboard API router (TASK-009).

Read-only presentation layer for the Recovery Agent pipeline.
This router does NOT:
- implement or duplicate policy logic
- call Razorpay directly
- call Ollama / Qwen directly
- authorize recovery independently
- override any backend policy decision

All recovery decisions come from the existing pipeline components.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from app.config import settings

from app.audit.result import AuditOutcome, AuditRecord
from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.escalation.handler import EscalationHandler
from app.executor.mock import MockExecutor
from app.models.payment_event import FailedTransactionEvent
from app.persistence.store import RecoveryStateStore
from app.pipeline.engine import RecoveryPipeline
from app.pipeline.result import PipelineResult
from app.policy.engine import RecoveryPolicyEngine
from app.reasoning.engine import RecoveryReasoner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# ---------------------------------------------------------------------------
# Shared pipeline instance (in-memory SQLite, mock executor)
# ---------------------------------------------------------------------------

# Persistent audit store: honors DATABASE_URL (defaults to a file DB) so
# records survive a backend restart instead of vanishing with an in-memory DB.
_audit_logger = AuditLogger(settings.database_url)
# Durable idempotency + attempt history, shared with the executor so recovery
# state survives a backend restart.
_state_store = RecoveryStateStore(settings.database_url)
_pipeline = RecoveryPipeline(
    classifier=FailureClassifier(),
    policy_engine=RecoveryPolicyEngine(),
    reasoner=RecoveryReasoner(),
    executor=MockExecutor(state_store=_state_store),
    escalation_handler=EscalationHandler(),
    audit_logger=_audit_logger,
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DashboardResponse(BaseModel):
    """Flattened, frontend-friendly view of a pipeline result.

    Every field is a direct projection from the pipeline result.
    No policy logic is applied here.
    """

    payment_id: str = Field(..., description="Razorpay payment ID")
    event_id: str = Field(..., description="Unique event identifier")

    # Classification
    failure_category: str | None = Field(
        default=None, description="Classified failure category"
    )
    classification_reason: str | None = Field(
        default=None, description="Classifier reason"
    )

    # Policy (projected as-is from the pipeline)
    policy_action: str | None = Field(
        default=None, description="Policy-prescribed action"
    )
    policy_reason: str | None = Field(
        default=None, description="Policy decision reason"
    )
    automatic_recovery_allowed: bool | None = Field(
        default=None, description="Whether automatic recovery was authorized by policy"
    )

    # Reasoning
    reasoning_recommendation: str | None = Field(
        default=None, description="Qwen recommendation text"
    )
    reasoning_explanation: str | None = Field(
        default=None, description="Qwen explanation text"
    )
    reasoning_success: bool | None = Field(
        default=None, description="Whether the reasoning layer succeeded"
    )

    # Execution
    execution_status: str | None = Field(
        default=None, description="Executor outcome status"
    )
    execution_reason: str | None = Field(
        default=None, description="Executor outcome reason"
    )

    # Escalation
    escalation_status: str | None = Field(
        default=None, description="Escalation status"
    )
    escalation_reason: str | None = Field(
        default=None, description="Escalation reason"
    )
    escalation_severity: str | None = Field(
        default=None, description="Escalation severity"
    )

    # Final outcome
    final_outcome: str = Field(
        ..., description="Final pipeline outcome"
    )

    # Metadata
    timestamp: str = Field(..., description="ISO 8601 pipeline timestamp")
    amount: int | None = Field(default=None, description="Transaction amount in paise")
    attempt_number: int | None = Field(
        default=None, description="Attempt number"
    )
    error: str | None = Field(
        default=None, description="Pipeline error, if any"
    )


class AuditLogResponse(BaseModel):
    """List of audit records."""

    records: list[AuditRecord] = Field(
        default_factory=list, description="Audit log entries (this page)"
    )
    count: int = Field(default=0, description="Number of records in this page")
    total: int = Field(
        default=0, description="Total matching records, ignoring pagination"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pipeline_to_response(result: PipelineResult, event: FailedTransactionEvent | None = None) -> DashboardResponse:
    """Project a PipelineResult into a DashboardResponse.

    No policy logic — pure field extraction.
    """
    amount = event.amount if event is not None else None
    attempt_number = event.attempt_number if event is not None else None

    return DashboardResponse(
        payment_id=result.payment_id,
        event_id=result.event_id,
        # Classification
        failure_category=(
            result.classification.category.value
            if result.classification is not None
            else None
        ),
        classification_reason=(
            result.classification.reason
            if result.classification is not None
            else None
        ),
        # Policy
        policy_action=(
            result.policy_decision.action.value
            if result.policy_decision is not None
            else None
        ),
        policy_reason=(
            result.policy_decision.reason
            if result.policy_decision is not None
            else None
        ),
        automatic_recovery_allowed=(
            result.policy_decision.automatic_recovery_allowed
            if result.policy_decision is not None
            else None
        ),
        # Reasoning
        reasoning_recommendation=(
            result.reasoning.recommendation
            if result.reasoning is not None
            else None
        ),
        reasoning_explanation=(
            result.reasoning.explanation
            if result.reasoning is not None
            else None
        ),
        reasoning_success=(
            result.reasoning.success
            if result.reasoning is not None
            else None
        ),
        # Execution
        execution_status=(
            result.execution.status.value
            if result.execution is not None
            else None
        ),
        execution_reason=(
            result.execution.reason
            if result.execution is not None
            else None
        ),
        # Escalation
        escalation_status=(
            result.escalation.status.value
            if result.escalation is not None
            else None
        ),
        escalation_reason=(
            result.escalation.reason
            if result.escalation is not None
            else None
        ),
        escalation_severity=(
            result.escalation.severity.value
            if result.escalation is not None
            else None
        ),
        # Final outcome
        final_outcome=result.final_outcome.value,
        # Metadata
        timestamp=result.timestamp.isoformat(),
        amount=amount,
        attempt_number=attempt_number,
        error=result.error,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/process", response_model=DashboardResponse)
def process_payment(event: FailedTransactionEvent) -> DashboardResponse:
    """Run a payment event through the recovery pipeline.

    The pipeline makes all decisions. This endpoint only projects
    the result for display.
    """
    try:
        result = _pipeline.process(event)
    except Exception as exc:
        logger.error("Pipeline processing failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}") from exc

    return _pipeline_to_response(result, event)


@router.get("/audit", response_model=AuditLogResponse)
def get_audit_log(
    limit: int | None = Query(
        default=None, ge=1, le=500,
        description="Max records to return; omit to return all",
    ),
    offset: int = Query(default=0, ge=0, description="Records to skip (pagination)"),
    outcome: str | None = Query(
        default=None, description="Filter by final_outcome (e.g. 'recovered')"
    ),
) -> AuditLogResponse:
    """Return audit records. Read-only.

    Supports pagination (``limit``/``offset``) and an optional
    ``final_outcome`` filter. Omitting ``limit`` returns all matching records
    for backward compatibility. ``count`` is the size of the returned page;
    ``total`` is the full matching count.
    """
    try:
        records = _audit_logger.list_records(limit=limit, offset=offset, outcome=outcome)
        total = _audit_logger.count_records(outcome=outcome)
    except Exception as exc:
        logger.error("Audit log retrieval failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Audit log error: {exc}"
        ) from exc

    return AuditLogResponse(records=records, count=len(records), total=total)
