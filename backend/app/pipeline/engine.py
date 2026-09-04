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
    ) -> None:
        self.classifier = classifier
        self.policy_engine = policy_engine
        self.reasoner = reasoner
        self.executor = executor
        self.escalation_handler = escalation_handler
        self.audit_logger = audit_logger
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

            # 2. Policy Evaluation
            try:
                policy_decision = self.policy_engine.evaluate(payment_event, classification)
            except Exception as exc:
                logger.warning("Pipeline: Policy boundary failed: %s", exc)

            # 3. Reasoning Layer
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

            # 4. Execution
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

        # 5. Escalation Evaluation
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

        # 6. Audit
        audit_write = None
        try:
            audit_write = self.audit_logger.record(
                payment_event,
                classification=classification,
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
            policy_decision=policy_decision,
            reasoning=reasoning,
            execution=execution,
            escalation=escalation,
            audit_write=audit_write,
            final_outcome=final_outcome,
            error=error_msg,
            timestamp=now,
        )

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
