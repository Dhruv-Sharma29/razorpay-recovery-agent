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
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from app.config import settings

from app.audit.result import AuditOutcome, AuditRecord
from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.escalation.handler import EscalationHandler
from app.executor.mock import MockExecutor
from app.models.payment_event import FailedTransactionEvent
from app.evaluation.harness import classify_funnel_stage
from app.ingestion.generator import generate_dataset
from app.persistence.store import RecoveryStateStore
from app.scheduler import run_due_jobs
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
# Request pipeline: carries the state store, so an action with a policy
# cooldown is persisted as a scheduled job and reported pending.
_pipeline = RecoveryPipeline(
    classifier=FailureClassifier(),
    policy_engine=RecoveryPolicyEngine(),
    reasoner=RecoveryReasoner(),
    executor=MockExecutor(state_store=_state_store),
    escalation_handler=EscalationHandler(),
    audit_logger=_audit_logger,
    state_store=_state_store,
)

# Worker pipeline: deliberately has NO state store, so a due job actually
# executes instead of rescheduling itself.
_worker_pipeline = RecoveryPipeline(
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
    reasoning_is_fallback: bool | None = Field(
        default=None,
        description="True when the deterministic fallback produced the text",
    )
    reasoning_model: str | None = Field(
        default=None, description="Model that produced the explanation"
    )
    root_cause_plain: str | None = Field(
        default=None, description="Plain-language root cause"
    )
    why_appropriate: str | None = Field(
        default=None, description="Why the policy action fits this failure"
    )
    customer_message: str | None = Field(
        default=None, description="Suggested customer-facing copy"
    )
    escalation_summary: str | None = Field(
        default=None, description="Summary for a human reviewer"
    )

    # Execution
    execution_status: str | None = Field(
        default=None, description="Executor outcome status"
    )
    execution_reason: str | None = Field(
        default=None, description="Executor outcome reason"
    )
    payment_status: str | None = Field(
        default=None,
        description="Simulated gateway payment status (captured/failed/not_attempted)",
    )
    amount_recovered: int | None = Field(
        default=None, description="Amount actually recovered, in paise"
    )
    simulated: bool | None = Field(
        default=None,
        description="True when no real gateway was contacted (always true today)",
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
        reasoning_is_fallback=(
            result.reasoning.is_fallback
            if result.reasoning is not None
            else None
        ),
        reasoning_model=(
            result.reasoning.model_id if result.reasoning is not None else None
        ),
        root_cause_plain=(
            result.reasoning.root_cause_plain
            if result.reasoning is not None
            else None
        ),
        why_appropriate=(
            result.reasoning.why_appropriate
            if result.reasoning is not None
            else None
        ),
        customer_message=(
            result.reasoning.customer_message
            if result.reasoning is not None
            else None
        ),
        escalation_summary=(
            result.reasoning.escalation_summary
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
        payment_status=(
            result.execution.payment_status
            if result.execution is not None
            else None
        ),
        amount_recovered=(
            result.execution.amount_recovered
            if result.execution is not None
            else None
        ),
        simulated=(
            result.execution.simulated
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


@router.post("/run-scheduled")
def run_scheduled(
    now: str | None = Query(
        default=None,
        description=(
            "ISO-8601 evaluation time. Lets a demo run a 24h cooldown "
            "without waiting. Defaults to the current time."
        ),
    ),
) -> dict[str, Any]:
    """Execute every scheduled retry whose cooldown has elapsed."""
    if now is None:
        evaluated_at = datetime.now(timezone.utc)
    else:
        try:
            evaluated_at = datetime.fromisoformat(now)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid 'now' timestamp: {now}"
            ) from exc
        if evaluated_at.tzinfo is None:
            evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)

    try:
        report = run_due_jobs(_state_store, _worker_pipeline, now=evaluated_at)
    except Exception as exc:
        logger.error("Scheduler run failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Scheduler error: {exc}"
        ) from exc

    payload = report.as_dict()
    payload["evaluated_at"] = evaluated_at.isoformat()
    return payload


def _fresh_batch(count: int, seed: int | None = None) -> list[FailedTransactionEvent]:
    """Generate ``count`` synthetic failed events with brand-new identifiers.

    Fresh ids on every run are what make the batch runner repeatable: runs
    accumulate in the audit log instead of colliding on idempotency. The
    seed is random by default so repeated demo runs produce different
    numbers; ``evaluate.py`` keeps its fixed seed for reproducibility.
    """
    pool_seed = seed if seed is not None else random.randrange(1_000_000)
    pool = generate_dataset(seed=pool_seed, total=100)

    events: list[FailedTransactionEvent] = []
    for i in range(count):
        template = pool[i % len(pool)]
        suffix = uuid.uuid4().hex[:12]
        events.append(
            template.model_copy(
                update={
                    "event_id": f"evt_batch_{suffix}",
                    "razorpay_payment_id": f"pay_batch_{suffix}",
                }
            )
        )
    return events


@router.post("/run-batch")
def run_batch(
    count: int = Query(default=25, ge=1, le=500, description="Events to process"),
    run_scheduler: bool = Query(
        default=True,
        description="Run deferred retries to completion before measuring",
    ),
    seed: int | None = Query(
        default=None, description="Fix the generator seed for a reproducible batch"
    ),
) -> dict[str, Any]:
    """Process a fresh batch of synthetic failures and measure money recovered.

    Repeatable by design: every call generates new event ids, so runs
    accumulate rather than deduplicating against earlier ones.
    """
    started = datetime.now(timezone.utc)
    events = _fresh_batch(count, seed)

    attempted_amount = 0
    recovered_amount = 0
    audit_ids: list[str] = []
    outcomes: dict[str, int] = {}
    funnel = {"raw": 0, "needed_signal": 0, "contacted": 0, "confirmed_recovered": 0}
    by_category: dict[str, dict[str, Any]] = {}

    for event in events:
        try:
            result = _pipeline.process(event)
        except Exception as exc:  # noqa: BLE001
            logger.error("Batch: pipeline failed for %s: %s", event.event_id, exc)
            continue

        attempted_amount += event.amount or 0
        outcome = result.final_outcome.value
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if result.audit_write is not None and result.audit_write.record is not None:
            audit_ids.append(result.audit_write.record.audit_id)

        execution = result.execution
        got = (
            execution.amount_recovered
            if execution is not None and isinstance(execution.amount_recovered, int)
            else 0
        )
        recovered_amount += got

        category = (
            result.classification.category.value
            if result.classification is not None
            else "unknown"
        )
        bucket = by_category.setdefault(
            category,
            {"count": 0, "recovered_count": 0, "attempted_amount": 0,
             "recovered_amount": 0, "recovery_rate_amount": 0.0},
        )
        bucket["count"] += 1
        bucket["attempted_amount"] += event.amount or 0
        bucket["recovered_amount"] += got
        if got > 0:
            bucket["recovered_count"] += 1

        stages = classify_funnel_stage(
            automatic_recovery_allowed=bool(
                result.policy_decision is not None
                and result.policy_decision.automatic_recovery_allowed
            ),
            escalated=outcome == "escalated",
            executed=bool(execution is not None and execution.executed is True),
            recovered_amount=got,
        )
        for stage, hit in stages.items():
            funnel[stage] += int(hit)

    # Deferred retries only move money once the worker runs them.
    scheduler_summary: dict[str, Any] | None = None
    if run_scheduler:
        # Far enough ahead that every cooldown in this batch has elapsed.
        horizon = datetime.now(timezone.utc) + timedelta(days=365)
        report = run_due_jobs(_state_store, _worker_pipeline, now=horizon)
        scheduler_summary = report.as_dict()
        recovered_amount += report.amount_recovered
        funnel["contacted"] += report.recovered + report.failed
        funnel["confirmed_recovered"] += report.recovered
        # Attribute deferred recoveries to the scenario they came from, or
        # the per-scenario card would contradict the batch total.
        for cat, amount in report.amount_by_category.items():
            bucket = by_category.setdefault(
                cat,
                {"count": 0, "recovered_count": 0, "attempted_amount": 0,
                 "recovered_amount": 0, "recovery_rate_amount": 0.0},
            )
            bucket["recovered_amount"] += amount
        for cat, hits in report.count_by_category.items():
            by_category[cat]["recovered_count"] += hits
        if report.recovered:
            outcomes["recovered"] = outcomes.get("recovered", 0) + report.recovered
            outcomes["pending"] = max(
                0, outcomes.get("pending", 0) - report.recovered
            )

    for bucket in by_category.values():
        if bucket["attempted_amount"] > 0:
            bucket["recovery_rate_amount"] = (
                bucket["recovered_amount"] / bucket["attempted_amount"]
            )

    processed = sum(outcomes.values())
    return {
        "transactions_processed": processed,
        "total_attempted_amount": attempted_amount,
        "total_recovered_amount": recovered_amount,
        "recovery_rate_by_amount": (
            recovered_amount / attempted_amount if attempted_amount else 0.0
        ),
        "recovery_rate_by_count": (
            funnel["confirmed_recovered"] / processed if processed else 0.0
        ),
        "outcomes": outcomes,
        "funnel": funnel,
        "by_scenario": [
            {"scenario": name, **values} for name, values in sorted(by_category.items())
        ],
        "audit_ids": audit_ids,
        "scheduler": scheduler_summary,
        "simulated": True,
        "duration_seconds": (
            datetime.now(timezone.utc) - started
        ).total_seconds(),
    }


@router.get("/risk")
def revenue_at_risk(
    limit: int = Query(default=5, ge=1, le=50, description="Rows per breakdown"),
) -> dict[str, Any]:
    """Revenue-at-risk rollups derived from the audit log.

    Read-only aggregation over records the pipeline already wrote — no
    reprocessing, no new decisions. "At risk" is the value of failed
    payments; "recovered" is what the executor actually captured.
    """
    records = _audit_logger.list_records()

    by_merchant: dict[str, dict[str, int]] = {}
    by_customer: dict[str, dict[str, int]] = {}
    subscription = {"count": 0, "at_risk_amount": 0, "mandate_issues": 0}
    total_at_risk = 0
    total_recovered = 0

    for r in records:
        amount = r.amount or 0
        recovered = r.amount_recovered or 0
        total_at_risk += amount
        total_recovered += recovered

        if r.merchant_id:
            m = by_merchant.setdefault(
                r.merchant_id,
                {"failures": 0, "at_risk_amount": 0, "recovered_amount": 0},
            )
            m["failures"] += 1
            m["at_risk_amount"] += amount
            m["recovered_amount"] += recovered

        if r.customer_ref:
            c = by_customer.setdefault(
                r.customer_ref, {"failures": 0, "at_risk_amount": 0}
            )
            c["failures"] += 1
            c["at_risk_amount"] += amount

        if r.transaction_type == "subscription":
            subscription["count"] += 1
            subscription["at_risk_amount"] += amount
            # An expired or paused mandate cannot be retried into success:
            # it needs the customer to re-authorize.
            if r.mandate_status in ("expired", "paused"):
                subscription["mandate_issues"] += 1

    merchants = sorted(
        (
            {"merchant_id": k, **v, "outstanding_amount": v["at_risk_amount"] - v["recovered_amount"]}
            for k, v in by_merchant.items()
        ),
        key=lambda m: m["outstanding_amount"],
        reverse=True,
    )[:limit]

    # Only customers who failed more than once are "repeat" failures.
    repeats = sorted(
        (
            {"customer_ref": k, **v}
            for k, v in by_customer.items()
            if v["failures"] > 1
        ),
        key=lambda c: (c["failures"], c["at_risk_amount"]),
        reverse=True,
    )[:limit]

    return {
        "total_at_risk_amount": total_at_risk,
        "total_recovered_amount": total_recovered,
        "outstanding_amount": total_at_risk - total_recovered,
        "records_considered": len(records),
        "by_merchant": merchants,
        "repeat_customers": repeats,
        "subscription_failures": subscription,
    }


@router.post("/reset")
def reset_state() -> dict[str, Any]:
    """Clear recovery state so a demo can re-run cleanly. Test-mode only.

    Deliberately does NOT delete audit records: the audit log is
    append-only, and that guarantee is the point of the system. Clearing
    idempotency, attempt history, and scheduled jobs is enough to make a
    fresh batch behave like a first run.
    """
    try:
        _state_store.clear()
    except Exception as exc:  # noqa: BLE001
        logger.error("Reset failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Reset error: {exc}") from exc
    return {
        "cleared": True,
        "recovery_state_cleared": True,
        "audit_log_preserved": True,
        "note": "Audit history is append-only and is never deleted.",
    }


@router.get("/scheduled")
def list_scheduled(
    status: str | None = Query(
        default=None, description="Filter by job status (pending/done/failed)"
    ),
) -> dict[str, Any]:
    """Read-only view of scheduled retry jobs."""
    jobs = _state_store.list_jobs(status=status)
    # event_json is the full stored payload; omit it from the listing.
    trimmed = [{k: v for k, v in job.items() if k != "event_json"} for job in jobs]
    return {"jobs": trimmed, "count": len(trimmed)}


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
