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
from app.escalation.handler import EscalationHandler
from app.executor.mock import MockExecutor
from app.models.payment_event import FailedTransactionEvent
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.reasoning.engine import RecoveryReasoner

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
    records: list[EvaluationRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class Evaluator:
    """Harness to evaluate the pipeline against a JSON dataset."""

    def __init__(self) -> None:
        # Use an in-memory audit logger and mock executor.
        # This prevents real Razorpay calls and avoids DB pollution.
        self.audit_logger = AuditLogger("sqlite:///:memory:")
        self.executor = MockExecutor()
        
        self.pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            reasoner=RecoveryReasoner(),  # Must be mocked externally in tests/run
            executor=self.executor,
            escalation_handler=EscalationHandler(),
            audit_logger=self.audit_logger,
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
            report.records.append(
                EvaluationRecord(
                    event_id=event.event_id,
                    payment_id=event.razorpay_payment_id,
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
                )
            )

        # Finalize classification accuracy
        if total_classifications > 0:
            report.classification_accuracy = (
                correct_classifications / total_classifications
            )

        return report
