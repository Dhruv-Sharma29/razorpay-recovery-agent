"""Evaluation harness (TASK-010).

Deterministically evaluates the existing RecoveryPipeline against
synthetic and held-out datasets without duplicating policy logic.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.audit.store import AuditLogger
from app.classifier.engine import FailureClassifier
from app.classifier.result import ClassificationResult
from app.escalation.handler import EscalationHandler
from app.executor.mock import MockExecutor
from app.models.payment_event import FailedTransactionEvent
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.policy.result import RecommendationStatus
from app.reasoning.engine import RecoveryReasoner
from app.recommendation.engine import RecoveryRecommender
from app.recommendation.result import RecoveryRecommendation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class EvaluationRecord(BaseModel):
    """Detailed record of a single evaluated transaction."""
    event_id: str
    payment_id: str
    expected_failure_category: str | None = None
    predicted_failure_category: str | None = None
    policy_action: str | None = None
    automatic_recovery_allowed: bool | None = None
    final_outcome: str
    is_false_automatic_recovery: bool = False
    error: str | None = None
    attempted_amount: int = 0
    recovered_amount: int = 0
    payment_status: str | None = None
    recommendation_revenue_at_risk: bool | None = None
    recommendation_risk_score: float | None = None
    recommendation_suggested_cause: str | None = None
    recommendation_suggested_action: str | None = None
    recommendation_confidence: float | None = None
    recommendation_status: str | None = None
    recommendation_is_fallback: bool | None = None
    recommendation_model: str | None = None
    audit_id: str | None = None


def _as_paise(value: Any) -> int:
    """Coerce an executor-reported amount to whole paise.

    The harness runs against arbitrary executor implementations (including
    test doubles), so anything that is not a real integer counts as zero
    rather than corrupting the money totals.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class FunnelCounts(BaseModel):
    """Raw events narrowed down to money actually recovered.

    Each stage is a real filter over the one above it, not a restatement:
    a case can need a signal without being contacted, and be contacted
    without the payment being captured.
    """

    raw: int = 0
    """Every event processed in the batch."""

    needed_signal: int = 0
    """Policy warranted an intervention (recovery allowed, or escalated)."""

    contacted: int = 0
    """An action was actually attempted against the payment."""

    confirmed_recovered: int = 0
    """The simulated payment was captured with a non-zero amount."""


def classify_funnel_stage(
    *,
    automatic_recovery_allowed: bool,
    escalated: bool,
    executed: bool,
    recovered_amount: int,
) -> dict[str, bool]:
    """Single definition of the funnel stages a case belongs to.

    Shared by ``evaluate.py`` and the batch endpoint so the funnel and the
    KPIs can never drift apart.
    """
    needed_signal = bool(automatic_recovery_allowed or escalated)
    return {
        "raw": True,
        "needed_signal": needed_signal,
        "contacted": bool(executed),
        "confirmed_recovered": recovered_amount > 0,
    }


class CategoryBreakdown(BaseModel):
    """Per-failure-category money and count rollup."""

    count: int = 0
    recovered_count: int = 0
    attempted_amount: int = 0
    recovered_amount: int = 0
    recovery_rate_amount: float = 0.0


class EvaluationReport(BaseModel):
    """Aggregate metrics and detailed records for a dataset evaluation."""
    dataset_name: str
    total_transactions: int = 0
    classification_accuracy: float = 0.0
    automatic_recovery_count: int = 0
    escalation_count: int = 0
    denial_count: int = 0
    execution_failure_count: int = 0
    unknown_unsafe_count: int = 0
    false_automatic_recovery_count: int = 0

    # AI recommendation quality.  These are computed against the dataset's
    # expected failure category, where ``unknown`` is the not-at-risk label.
    risk_detection_evaluated_count: int = 0
    risk_detection_true_positive_count: int = 0
    risk_detection_false_positive_count: int = 0
    risk_detection_false_negative_count: int = 0
    risk_detection_precision: float | None = None
    risk_detection_recall: float | None = None
    recommendation_model_generated_count: int = 0
    recommendation_fallback_count: int = 0
    recommendation_status_counts: dict[str, int] = Field(default_factory=dict)
    recommendation_acceptance_rate: float = 0.0
    recommendation_constraining_rate: float = 0.0
    recommendation_rejection_rate: float = 0.0

    # Policy isolation compares the actual pipeline decision with a fresh
    # deterministic baseline for the same event.
    policy_isolation_checked_count: int = 0
    policy_isolation_violation_count: int = 0
    policy_isolation_passed: bool = True

    false_escalation_count: int = 0
    false_escalation_cost: int = 0
    exception_reasons: dict[str, int] = Field(default_factory=dict)

    # Money moved. The brief's headline metric is measured recovery, not a
    # success count, so amounts are tracked alongside every outcome.
    total_attempted_amount: int = 0
    total_recovered_amount: int = 0
    amount_escalated: int = 0
    amount_failed: int = 0
    recovery_rate_by_amount: float = 0.0
    recovery_rate_by_count: float = 0.0

    by_category: dict[str, CategoryBreakdown] = Field(default_factory=dict)
    funnel: FunnelCounts = Field(default_factory=FunnelCounts)

    records: list[EvaluationRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class Evaluator:
    """Harness to evaluate the pipeline against a JSON dataset."""

    def __init__(self, recommender: RecoveryRecommender | None = None) -> None:
        # Use an in-memory audit logger and mock executor.
        # This prevents real Razorpay calls and avoids DB pollution.
        self.audit_logger = AuditLogger("sqlite:///:memory:")
        self.executor = MockExecutor()
        self.recommender = recommender or RecoveryRecommender(nim_api_key="")
        
        self.pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            reasoner=RecoveryReasoner(),  # Must be mocked externally in tests/run
            executor=self.executor,
            escalation_handler=EscalationHandler(),
            audit_logger=self.audit_logger,
            recommender=self.recommender,
        )

    def load_dataset(self, file_path: str | Path) -> list[dict[str, Any]]:
        """Safely load JSON array of failed transactions."""
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"Dataset not found: {path}")
            return []

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                logger.error(f"Dataset must be a JSON array: {path}")
                return []
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from {path}: {e}")
            return []

    def evaluate(self, dataset_name: str, file_path: str | Path) -> EvaluationReport:
        """Run the evaluation pipeline against a dataset."""
        raw_records = self.load_dataset(file_path)
        report = EvaluationReport(dataset_name=dataset_name)

        correct_classifications = 0
        total_classifications = 0
        processed_event_ids = set()

        for raw in raw_records:
            # 1. Parse Input & Dedup
            try:
                event = FailedTransactionEvent(**raw)
            except ValidationError as e:
                logger.warning(f"Skipping malformed record: {e}")
                continue
                
            if event.event_id in processed_event_ids:
                logger.warning(f"Skipping duplicate event_id: {event.event_id}")
                continue
            processed_event_ids.add(event.event_id)

            report.total_transactions += 1

            # 2. Process through pipeline
            try:
                result = self.pipeline.process(event)
            except Exception as e:
                logger.error(f"Pipeline error for {event.event_id}: {e}")
                report.records.append(
                    EvaluationRecord(
                        event_id=event.event_id,
                        payment_id=event.razorpay_payment_id,
                        final_outcome="error",
                        error=str(e),
                    )
                )
                continue

            # 3. Extract metrics
            expected_category = raw.get("failure_category")
            predicted_category = (
                result.classification.category.value
                if result.classification
                else None
            )

            # Check classification accuracy
            if expected_category:
                total_classifications += 1
                if expected_category == predicted_category:
                    correct_classifications += 1

            automatic_recovery_allowed = (
                result.policy_decision.automatic_recovery_allowed
                if result.policy_decision
                else False
            )

            final_outcome = result.final_outcome.value

            recommendation = (
                result.recommendation
                if isinstance(result.recommendation, RecoveryRecommendation)
                else None
            )
            if recommendation is not None:
                if recommendation.is_fallback:
                    report.recommendation_fallback_count += 1
                else:
                    report.recommendation_model_generated_count += 1
                raw_status = getattr(
                    getattr(result, "policy_decision", None),
                    "recommendation_status",
                    None,
                )
                if isinstance(raw_status, RecommendationStatus):
                    status = raw_status.value
                    report.recommendation_status_counts[status] = (
                        report.recommendation_status_counts.get(status, 0) + 1
                    )

                # Only labeled records are eligible for risk-quality metrics.
                # Known categories are the positive class; unknown is negative.
                if expected_category is not None:
                    report.risk_detection_evaluated_count += 1
                    expected_at_risk = expected_category != "unknown"
                    predicted_at_risk = recommendation.revenue_at_risk
                    if predicted_at_risk and expected_at_risk:
                        report.risk_detection_true_positive_count += 1
                    elif predicted_at_risk and not expected_at_risk:
                        report.risk_detection_false_positive_count += 1
                    elif not predicted_at_risk and expected_at_risk:
                        report.risk_detection_false_negative_count += 1

            baseline_decision = None
            if isinstance(result.classification, ClassificationResult):
                baseline_decision = self.pipeline.policy_engine.evaluate(
                    event, result.classification
                )
            if baseline_decision is not None and result.policy_decision is not None:
                report.policy_isolation_checked_count += 1
                authorized_above_baseline = (
                    result.policy_decision.automatic_recovery_allowed
                    and not baseline_decision.automatic_recovery_allowed
                )
                action_changed = (
                    result.policy_decision.action != baseline_decision.action
                )
                if authorized_above_baseline or action_changed:
                    report.policy_isolation_violation_count += 1
                    report.policy_isolation_passed = False

            # Increment specific counts
            if automatic_recovery_allowed:
                report.automatic_recovery_count += 1
                
            if final_outcome == "escalated":
                report.escalation_count += 1
            elif final_outcome == "denied":
                report.denial_count += 1
            elif final_outcome == "execution_failed":
                report.execution_failure_count += 1
                
            if predicted_category == "unknown":
                report.unknown_unsafe_count += 1

            # --- Money moved -------------------------------------------------
            # Attempted is the full value at stake; recovered comes from the
            # executor's simulated payment result, never inferred from the
            # outcome label.
            attempted_amount = _as_paise(event.amount)
            execution = result.execution
            recovered_amount = (
                _as_paise(execution.amount_recovered)
                if execution is not None
                else 0
            )
            payment_status = (
                execution.payment_status if execution is not None else None
            )
            if not isinstance(payment_status, str):
                payment_status = None

            report.total_attempted_amount += attempted_amount
            report.total_recovered_amount += recovered_amount
            if final_outcome == "escalated":
                report.amount_escalated += attempted_amount
                if (
                    expected_category not in (None, "unknown")
                    and baseline_decision is not None
                    and baseline_decision.automatic_recovery_allowed
                ):
                    report.false_escalation_count += 1
                    report.false_escalation_cost += attempted_amount
            elif recovered_amount == 0:
                report.amount_failed += attempted_amount

            if result.error:
                key = "pipeline_error"
                report.exception_reasons[key] = report.exception_reasons.get(key, 0) + 1
            if recommendation is not None and recommendation.fallback_reason is not None:
                key = f"recommendation:{recommendation.fallback_reason.value}"
                report.exception_reasons[key] = report.exception_reasons.get(key, 0) + 1

            stages = classify_funnel_stage(
                automatic_recovery_allowed=bool(automatic_recovery_allowed),
                escalated=final_outcome == "escalated",
                executed=bool(
                    execution is not None and execution.executed is True
                ),
                recovered_amount=recovered_amount,
            )
            report.funnel.raw += 1
            report.funnel.needed_signal += int(stages["needed_signal"])
            report.funnel.contacted += int(stages["contacted"])
            report.funnel.confirmed_recovered += int(stages["confirmed_recovered"])

            bucket_key = predicted_category or "unknown"
            bucket = report.by_category.setdefault(
                bucket_key, CategoryBreakdown()
            )
            bucket.count += 1
            bucket.attempted_amount += attempted_amount
            bucket.recovered_amount += recovered_amount
            if recovered_amount > 0:
                bucket.recovered_count += 1

            # 4. Check for False Automatic Recovery
            # If the pipeline allowed recovery, but the ground truth label suggests
            # it was unsafe (e.g. 'unknown', or if limits were exceeded).
            # Note: The true bounds are known to the policy, but we can verify against
            # the ground truth category. If the ground truth category is 'unknown', it
            # should never have been recovered.
            # We also check amount limits and attempt limits against ground truth.
            is_false_auto_recovery = False
            if automatic_recovery_allowed:
                is_unsafe_category = expected_category == "unknown"
                is_over_limit = event.amount > 500000
                is_over_attempts = event.attempt_number > 3
                if is_unsafe_category or is_over_limit or is_over_attempts:
                    is_false_auto_recovery = True
                    report.false_automatic_recovery_count += 1

            # 5. Record detail
            recommendation_status = None
            raw_status = getattr(
                getattr(result, "policy_decision", None),
                "recommendation_status",
                None,
            )
            if isinstance(raw_status, RecommendationStatus):
                recommendation_status = raw_status.value
            elif isinstance(raw_status, str):
                recommendation_status = raw_status

            audit_id = getattr(getattr(result, "audit_write", None), "audit_id", None)
            if not isinstance(audit_id, str):
                audit_id = None

            report.records.append(
                EvaluationRecord(
                    event_id=event.event_id,
                    payment_id=event.razorpay_payment_id,
                    attempted_amount=attempted_amount,
                    recovered_amount=recovered_amount,
                    payment_status=payment_status,
                    expected_failure_category=expected_category,
                    predicted_failure_category=predicted_category,
                    policy_action=(
                        result.policy_decision.action.value
                        if result.policy_decision
                        else None
                    ),
                    automatic_recovery_allowed=automatic_recovery_allowed,
                    final_outcome=final_outcome,
                    is_false_automatic_recovery=is_false_auto_recovery,
                    error=result.error,
                    recommendation_revenue_at_risk=(
                        recommendation.revenue_at_risk
                        if recommendation is not None else None
                    ),
                    recommendation_risk_score=(
                        recommendation.risk_score
                        if recommendation is not None else None
                    ),
                    recommendation_suggested_cause=(
                        recommendation.suggested_cause.value
                        if recommendation is not None
                        and recommendation.suggested_cause is not None
                        else None
                    ),
                    recommendation_suggested_action=(
                        recommendation.suggested_action.value
                        if recommendation is not None
                        and recommendation.suggested_action is not None
                        else None
                    ),
                    recommendation_confidence=(
                        recommendation.confidence
                        if recommendation is not None else None
                    ),
                    recommendation_status=recommendation_status,
                    recommendation_is_fallback=(
                        recommendation.is_fallback
                        if recommendation is not None else None
                    ),
                    recommendation_model=(
                        recommendation.model_id
                        if recommendation is not None else None
                    ),
                    audit_id=audit_id,
                )
            )

        # Finalize classification accuracy
        if total_classifications > 0:
            report.classification_accuracy = (
                correct_classifications / total_classifications
            )

        status_total = sum(report.recommendation_status_counts.values())
        if status_total > 0:
            report.recommendation_acceptance_rate = (
                report.recommendation_status_counts.get(
                    RecommendationStatus.ACCEPTED.value, 0
                ) / status_total
            )
            report.recommendation_constraining_rate = (
                report.recommendation_status_counts.get(
                    RecommendationStatus.CONSTRAINED.value, 0
                ) / status_total
            )
            report.recommendation_rejection_rate = (
                report.recommendation_status_counts.get(
                    RecommendationStatus.REJECTED.value, 0
                ) / status_total
            )

        denominator = (
            report.risk_detection_true_positive_count
            + report.risk_detection_false_positive_count
        )
        if denominator > 0:
            report.risk_detection_precision = (
                report.risk_detection_true_positive_count / denominator
            )
        risk_actuals = (
            report.risk_detection_true_positive_count
            + report.risk_detection_false_negative_count
        )
        if risk_actuals > 0:
            report.risk_detection_recall = (
                report.risk_detection_true_positive_count / risk_actuals
            )

        # Finalize money rates. Rate-by-amount is the headline: it weights a
        # large recovered payment above a small one, which a count cannot.
        if report.total_attempted_amount > 0:
            report.recovery_rate_by_amount = (
                report.total_recovered_amount / report.total_attempted_amount
            )
        if report.total_transactions > 0:
            recovered_count = sum(
                1 for r in report.records if r.recovered_amount > 0
            )
            report.recovery_rate_by_count = (
                recovered_count / report.total_transactions
            )
        for bucket in report.by_category.values():
            if bucket.attempted_amount > 0:
                bucket.recovery_rate_amount = (
                    bucket.recovered_amount / bucket.attempted_amount
                )

        return report
