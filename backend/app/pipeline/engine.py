"""End-to-End Recovery Agent Pipeline (TASK-008).

Orchestrates the deterministic recovery workflow.
The Policy Engine is the authoritative decision-maker.
Every component boundary fails safely.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.audit.result import AuditOutcome
from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.escalation.handler import EscalationHandler
from app.executor.base import RecoveryExecutor
from app.executor.result import ExecutionResult, ExecutionStatus
from app.models.payment_event import FailedTransactionEvent
from app.outreach.dispatcher import OutreachResult, SimulatedOutreachDispatcher
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
        outreach: SimulatedOutreachDispatcher | None = None,
        allow_model_action_choice: bool = True,
        model_action_choice_min_confidence: float | None = None,
    ) -> None:
        self.classifier = classifier
        self.policy_engine = policy_engine
        self.reasoner = reasoner
        self.executor = executor
        self.escalation_handler = escalation_handler
        self.audit_logger = audit_logger
        self.recommender = recommender
        # Delivers the drafted message for actions that need the customer to
        # act. None disables outreach entirely.
        self.outreach = outreach
        # Control arm for A/B: when False the policy's prescribed action is
        # always used, so the two arms differ only by the advisor's choice.
        self.allow_model_action_choice = allow_model_action_choice
        self.model_action_choice_min_confidence = (
            settings.model_action_choice_min_confidence
            if model_action_choice_min_confidence is None
            else model_action_choice_min_confidence
        )
        # Measured outcomes change slowly and a batch asks for them once per
        # event, so they are cached briefly rather than re-aggregated 60 times.
        self._outcome_cache: list[dict] = []
        self._outcome_cache_at: datetime | None = None
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
        outreach = None
        # Who chose the action. Defaults to the policy's own prescription.
        action_source = "policy"
        delay_source = "policy"
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
                        payment_event,
                        classification,
                        approved_history,
                        # Ask for a choice from the real menu, and give it the
                        # evidence needed to make one.
                        available_actions=[
                            action.value
                            for action in self.policy_engine.permitted_actions_for(
                                classification.category
                                if classification is not None
                                else None
                            )
                        ],
                        observed_outcomes=self._observed_outcomes(),
                        cooldown_window=self.policy_engine.cooldown_window_for(
                            classification.category
                            if classification is not None
                            else None
                        ),
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

            # 3b. Bounded action choice.
            # The policy decides WHETHER to act and publishes the set of
            # actions it authorises. The advisor may only pick among that
            # set — it cannot add an action, and cannot touch
            # automatic_recovery_allowed. Anything outside the set is ignored.
            #
            # It must also be confident. An unsure advisor overriding a
            # deterministic default is how the A/B lost ground, so a low
            # confidence answer is recorded and then not acted on.
            if (
                self.allow_model_action_choice
                and policy_decision is not None
                and recommendation is not None
                and recommendation.suggested_action is not None
                and policy_decision.automatic_recovery_allowed
                and recommendation.suggested_action in policy_decision.permitted_actions
                and recommendation.confidence >= self.model_action_choice_min_confidence
                and recommendation.suggested_action != policy_decision.action
            ):
                policy_decision = policy_decision.model_copy(
                    update={"action": recommendation.suggested_action}
                )
                action_source = "model"

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

            # 4b. Bounded timing choice.
            # For insufficient funds the cooldown IS the intervention, so
            # letting the advisor move it is worth more than letting it pick
            # the action. Same shape of bound: policy publishes a window, the
            # advisor may move inside it, and a value outside is discarded
            # rather than clamped — silently snapping 90 days to the maximum
            # would record a choice the model never made.
            cooldown = (
                getattr(policy_decision, "cooldown_seconds", 0) or 0
                if policy_decision is not None
                else 0
            )
            if (
                self.allow_model_action_choice
                and policy_decision is not None
                and recommendation is not None
                and recommendation.suggested_delay_seconds is not None
                and policy_decision.automatic_recovery_allowed
                and cooldown > 0
                and recommendation.confidence
                >= self.model_action_choice_min_confidence
                and policy_decision.cooldown_min_seconds
                <= recommendation.suggested_delay_seconds
                <= policy_decision.cooldown_max_seconds
            ):
                cooldown = recommendation.suggested_delay_seconds
                delay_source = "model"

            # 5. Execution
            if policy_decision and policy_decision.automatic_recovery_allowed:
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

        # 6. Customer outreach for actions that need the customer to act.
        if (
            self.outreach is not None
            and policy_decision is not None
            and policy_decision.automatic_recovery_allowed
            and execution is not None
        ):
            try:
                outreach = self.outreach.dispatch(
                    payment_event,
                    policy_decision.action,
                    reasoning.customer_message if reasoning is not None else None,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pipeline: Outreach boundary failed: %s", exc)
                outreach = OutreachResult(
                    attempted=True, delivered=False,
                    reason=f"Outreach failed: {exc}",
                )

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
            action_source=action_source,
            delay_source=delay_source,
            outreach=outreach,
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

    def _observed_outcomes(self, ttl_seconds: int = 30) -> list[dict]:
        """Recovery rates measured from this system's own audit log.

        Cached for a short window: the numbers move slowly, and re-running the
        aggregate for every event of a batch would cost more than it informs.
        Any failure yields an empty list — the advisor then reasons without
        the evidence, which is exactly how it behaved before.
        """
        if self.audit_logger is None:
            return []
        now = datetime.now(timezone.utc)
        if (
            self._outcome_cache_at is not None
            and (now - self._outcome_cache_at).total_seconds() < ttl_seconds
        ):
            return self._outcome_cache
        try:
            # One observation proves nothing; require a couple before showing
            # a rate the advisor might act on.
            self._outcome_cache = self.audit_logger.outcome_stats(min_observations=3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pipeline: outcome stats unavailable: %s", exc)
            self._outcome_cache = []
        self._outcome_cache_at = now
        return self._outcome_cache

    def process_batch(
        self,
        events: list[FailedTransactionEvent],
        max_workers: int = 5,
        on_result: Callable[[FailedTransactionEvent, PipelineResult | None], None] | None = None,
    ) -> list[PipelineResult | None]:
        """Process a batch of events with bounded concurrency.

        Uses a thread pool to parallelize I/O bound operations (like reasoning calls)
        across multiple events, while retaining deterministic order.

        ``on_result`` fires from the worker thread the moment an event finishes,
        which is what lets a caller stream progress rather than wait for the
        whole batch. It must be thread-safe, and it must never raise — a
        reporting failure cannot be allowed to lose a processed event.
        """
        import concurrent.futures
        
        results: list[PipelineResult | None] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            def _safe_process(ev: FailedTransactionEvent) -> PipelineResult | None:
                result: PipelineResult | None
                try:
                    result = self.process(ev)
                except Exception as exc:
                    logger.exception("Pipeline: fatal error processing event %s: %s", ev.event_id, exc)
                    result = PipelineResult(
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
                if on_result is not None:
                    try:
                        on_result(ev, result)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "Pipeline: on_result callback failed for %s", ev.event_id
                        )
                return result

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
            recovery_delay_seconds=cooldown_seconds,
            timestamp=now,
        )
