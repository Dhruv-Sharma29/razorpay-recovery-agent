"""End-to-End Recovery Agent Pipeline (TASK-008).

Orchestrates the deterministic recovery workflow.
The Policy Engine is the authoritative decision-maker.
Every component boundary fails safely.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.audit.result import AuditOutcome
from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.escalation.handler import EscalationHandler
from app.executor.base import RecoveryExecutor
from app.executor.result import ExecutionResult, ExecutionStatus
from app.models.payment_event import FailedTransactionEvent
from app.persistence.store import RecoveryStateStore
from app.pipeline.result import PipelineResult
from app.policy.engine import RecoveryPolicyEngine
from app.policy.result import PolicyDecision
from app.recommendation.engine import RecoveryRecommender
from app.recommendation.result import (
    ApprovedPaymentHistory,
    RecoveryRecommendation,
    RecommendationFallbackReason,
)
from app.reasoning.engine import RecoveryReasoner
from app.reasoning.result import ReasoningResult

logger = logging.getLogger(__name__)


class RecoveryPipeline:
    """Orchestrates the end-to-end recovery workflow.

    Dependencies are injected to allow for testing with mocks.
    The pipeline never duplicates logic from its components and never
    overrides the policy engine.
    """

    def __init__(
        self,
        classifier: FailureClassifier,
        policy_engine: RecoveryPolicyEngine,
        reasoner: RecoveryReasoner,
        executor: RecoveryExecutor,
        escalation_handler: EscalationHandler,
        audit_logger: AuditLogger,
        state_store: RecoveryStateStore | None = None,
        recommender: RecoveryRecommender | None = None,
    ) -> None:
        self.classifier = classifier
        self.policy_engine = policy_engine
        self.reasoner = reasoner
        self.executor = executor
        self.escalation_handler = escalation_handler
        self.audit_logger = audit_logger
        self.recommender = recommender
        # When present, actions carrying a policy cooldown are persisted as
        # scheduled jobs instead of running immediately.
        self.state_store = state_store

    def process(self, payment_event: FailedTransactionEvent) -> PipelineResult:
        """Process a payment failure through the end-to-end workflow."""
        now = datetime.now(timezone.utc)
        
        # We need these strings even if the event is totally broken
        payment_id = getattr(payment_event, "razorpay_payment_id", "unknown")
        event_id = getattr(payment_event, "event_id", "unknown")

        classification = None
        recommendation = None
        policy_decision = None
        reasoning = None
        execution = None
        escalation = None
        error_msg = None
        
        # Guard: invalid input
        if not isinstance(payment_event, FailedTransactionEvent):
            error_msg = "Invalid payment event provided to pipeline"
            logger.error(error_msg)
        else:
            # 1. Classification
            try:
                classification = self.classifier.classify(payment_event)
            except Exception as exc:
                logger.warning("Pipeline: Classification boundary failed: %s", exc)

            # 2. AI recommendation (advisory only)
            # The classifier runs independently first so model output always
            # has a deterministic validation baseline.
            if self.recommender is not None:
                try:
                    approved_history = self._approved_payment_history(payment_event)
                    recommendation = self.recommender.recommend(
                        payment_event, classification, approved_history
                    )
                except Exception as exc:
                    logger.warning("Pipeline: Recommendation boundary failed: %s", exc)
                    recommendation = RecoveryRecommendation(
                        success=False,
                        revenue_at_risk=False,
                        risk_score=0.0,
                        suggested_cause=(
                            classification.category if classification is not None else None
                        ),
                        suggested_action=None,
                        confidence=0.0,
                        evidence=["Recommendation service failed closed"],
                        model_id="pipeline_fallback",
                        is_fallback=True,
                        fallback_reason=RecommendationFallbackReason.PIPELINE_FAILURE,
                        error=str(exc),
                    )

            # 3. Policy Evaluation
            try:
                policy_decision = self.policy_engine.evaluate(
                    payment_event, classification, recommendation
                )
            except Exception as exc:
                logger.warning("Pipeline: Policy boundary failed: %s", exc)

            # 4. Reasoning Layer
            try:
                reasoning = self.reasoner.analyze(payment_event, classification, policy_decision)
            except Exception as exc:
                logger.warning("Pipeline: Reasoning boundary failed: %s", exc)
                # Ensure we have a safe fallback reasoning if the call fails entirely
                # The reasoner itself returns fallbacks on network issues, but this catches
                # unexpected logic crashes.
                reasoning = ReasoningResult(
                    success=False,
                    recommendation="Fallback due to pipeline reasoning exception",
                    explanation=f"Exception: {exc}",
                    confidence=0.0,
                    model_id="pipeline_fallback",
                    policy_action_allowed=(
                        policy_decision.automatic_recovery_allowed if policy_decision else False
                    ),
                    is_fallback=True,
                    error=str(exc)
                )

            # 5. Execution
            if policy_decision and policy_decision.automatic_recovery_allowed:
                cooldown = getattr(policy_decision, "cooldown_seconds", 0) or 0
                defer = cooldown > 0 and self.state_store is not None
                try:
                    if defer:
                        # The policy says wait. Persist the intent and report it
                        # as scheduled — calling this "recovered" now would claim
                        # money moved before any retry has run.
                        execution = self._schedule(
                            payment_event, policy_decision, cooldown, now
                        )
                    else:
                        execution = self.executor.execute(
                            payment_event, policy_decision
                        )
                except Exception as exc:
                    # Catastrophic executor failure (the executor itself raised
                    # rather than returning a structured result). Never leave
                    # execution as None: build a FAILED result so escalation
                    # opens (EXECUTOR_FAILURE) and the outcome is clearly an
                    # execution failure rather than a misleading recorded/denied.
                    logger.error("Pipeline: Executor raised unexpectedly: %s", exc)
                    action_attempted = (
                        policy_decision.action.value if policy_decision else "unknown"
                    )
                    execution = ExecutionResult(
                        status=ExecutionStatus.FAILED,
                        action_attempted=action_attempted,
                        payment_id=payment_id,
                        event_id=event_id,
                        executed=False,
                        execution_id=None,
                        idempotency_key=f"error:{payment_id}:{action_attempted}",
                        error=f"Executor raised unexpectedly: {exc}",
                        reason="Execution failed catastrophically; escalating for manual review.",
                    )
            else:
                # Do NOT execute if policy denies, is missing, or is malformed.
                pass

        # 6. Escalation Evaluation
        try:
            escalation = self.escalation_handler.handle(
                payment_event,
                policy_decision,
                classification=classification,
                reasoning=reasoning,
                execution=execution,
                timestamp=now,
            )
        except Exception as exc:
            logger.warning("Pipeline: Escalation boundary failed: %s", exc)

        # 7. Audit
        audit_write = None
        try:
            audit_write = self.audit_logger.record(
                payment_event,
                classification=classification,
                recommendation=recommendation,
                policy_decision=policy_decision,
                reasoning=reasoning,
                execution=execution,
                escalation=escalation,
                timestamp=now,
                extra={"error": error_msg} if error_msg else None,
            )
        except Exception as exc:
            logger.warning("Pipeline: Audit boundary failed: %s", exc)
            
        # Determine Final Outcome
        # We rely on the audit record's derived final outcome if available.
        if audit_write and audit_write.record:
            final_outcome = audit_write.record.final_outcome
        else:
            # Fallback derivation if audit completely crashed
            if escalation and escalation.status != "not_required":
                final_outcome = AuditOutcome.ESCALATED
            elif execution and execution.status == "failed":
                final_outcome = AuditOutcome.EXECUTION_FAILED
            elif execution and execution.status == "success":
                final_outcome = AuditOutcome.RECOVERED
            elif policy_decision and not policy_decision.automatic_recovery_allowed:
                final_outcome = AuditOutcome.DENIED
            elif not policy_decision or error_msg:
                final_outcome = AuditOutcome.AUDIT_FAILED # Could use a better status, but AUDIT_FAILED implies complete collapse
            else:
                final_outcome = AuditOutcome.RECORDED

        return PipelineResult(
            payment_id=payment_id,
            event_id=event_id,
            classification=classification,
            recommendation=recommendation,
            policy_decision=policy_decision,
            reasoning=reasoning,
            execution=execution,
            escalation=escalation,
            audit_write=audit_write,
            final_outcome=final_outcome,
            error=error_msg,
            timestamp=now,
        )

    def _approved_payment_history(
        self, payment_event: FailedTransactionEvent
    ) -> ApprovedPaymentHistory | None:
        """Fetch only bounded, redacted history for the advisory model."""
        provider = getattr(self.audit_logger, "get_approved_payment_history", None)
        if not callable(provider):
            return None
        try:
            history = provider(payment_event.customer_id)
            return history if isinstance(history, ApprovedPaymentHistory) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pipeline: approved history unavailable: %s", exc)
            return None

    def process_batch(
        self, events: list[FailedTransactionEvent], max_workers: int = 5
    ) -> list[PipelineResult | None]:
        """Process a batch of events with bounded concurrency.
        
        Uses a thread pool to parallelize I/O bound operations (like reasoning calls)
        across multiple events, while retaining deterministic order.
        """
        import concurrent.futures
        
        results: list[PipelineResult | None] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            def _safe_process(ev: FailedTransactionEvent) -> PipelineResult | None:
                try:
                    return self.process(ev)
                except Exception as exc:
                    logger.exception("Pipeline: fatal error processing event %s: %s", ev.event_id, exc)
                    return PipelineResult(
                        payment_id=ev.razorpay_payment_id,
                        event_id=ev.event_id,
                        classification=None,
                        policy_decision=None,
                        reasoning=None,
                        execution=None,
                        escalation=None,
                        audit_write=None,
                        final_outcome=AuditOutcome.AUDIT_FAILED,
                        error=str(exc),
                        timestamp=datetime.now(timezone.utc),
                    )

            # Map events to process() in parallel while preserving order
            futures = [executor.submit(_safe_process, event) for event in events]
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.error("Pipeline: batch item future failed: %s", exc)
                    results.append(None)
                    
        return results

    def _schedule(
        self,
        payment_event: FailedTransactionEvent,
        policy_decision: PolicyDecision,
        cooldown_seconds: int,
        now: datetime,
    ) -> ExecutionResult:
        """Persist a deferred recovery action and report it as SCHEDULED.

        No money moves here. The scheduler worker executes the job once the
        cooldown has elapsed.
        """
        eligible_at = now + timedelta(seconds=cooldown_seconds)
        action = policy_decision.action.value
        job_id = self.state_store.schedule_job(
            payment_id=payment_event.razorpay_payment_id,
            event_id=payment_event.event_id,
            action=action,
            next_eligible_at=eligible_at,
            event_json=payment_event.model_dump_json(),
        )
        return ExecutionResult(
            status=ExecutionStatus.SCHEDULED,
            action_attempted=action,
            payment_id=payment_event.razorpay_payment_id,
            event_id=payment_event.event_id,
            executed=False,
            execution_id=job_id,
            idempotency_key=f"scheduled:{payment_event.event_id}:{action}",
            error=None,
            reason=f"Scheduled for {eligible_at.isoformat()} ({cooldown_seconds}s cooldown)",
            payment_status="not_attempted",
            amount_recovered=0,
            timestamp=now,
        )
