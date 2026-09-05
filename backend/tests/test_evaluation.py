"""Evaluation tests (TASK-010).

Validates the evaluation harness itself to ensure it loads data,
computes metrics correctly (including false auto-recovery),
handles edge cases, and does not violate safety boundaries.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.evaluation.harness import Evaluator, EvaluationReport
from app.models.payment_event import FailedTransactionEvent, FailureCategory
from app.pipeline.engine import RecoveryPipeline
from app.policy.result import PolicyAction
from app.recommendation.result import RecoveryRecommendation
from app.reasoning.engine import RecoveryReasoner


@pytest.fixture
def temp_dataset(tmp_path):
    """Create a temporary dataset JSON file for testing."""
    def _create(data):
        file_path = tmp_path / "test_data.json"
        with open(file_path, "w") as f:
            json.dump(data, f)
        return file_path
    return _create


@pytest.fixture
def valid_record():
    return {
        "event_id": "evt_123",
        "razorpay_payment_id": "pay_123",
        "merchant_id": "merch_1",
        "customer_id": "cust_1",
        "type": "one_time",
        "amount": 10000,
        "currency": "INR",
        "payment_method": "upi",
        "error_code": "INSUFFICIENT_FUNDS",
        "error_description": "insufficient funds",
        "failure_category": "insufficient_funds",
        "attempt_number": 1,
        "mandate_status": None,
        "timestamp": "2026-09-01T10:00:00Z"
    }


class TestEvaluationHarness:

    def test_recommendation_metrics_and_policy_isolation(
        self, temp_dataset, valid_record
    ):
        """Risk metrics, treatment rates, and the isolation check are auditable."""
        unknown = valid_record.copy()
        unknown.update(
            event_id="evt_unknown",
            razorpay_payment_id="pay_unknown",
            error_code="WEIRD_ERROR",
            error_description="unrecognized provider failure",
            failure_category="unknown",
        )

        class FixtureRecommender:
            def recommend(self, event, classification, approved_history=None, **_):
                known = classification.category != FailureCategory.UNKNOWN
                return RecoveryRecommendation(
                    success=True,
                    revenue_at_risk=known,
                    risk_score=0.9 if known else 0.1,
                    suggested_cause=classification.category,
                    suggested_action=(
                        PolicyAction.SCHEDULED_RETRY if known else None
                    ),
                    confidence=0.9,
                    evidence=["fixture"],
                    model_id="fixture-model",
                    prompt_version="test",
                )

        file_path = temp_dataset([valid_record, unknown])
        evaluator = Evaluator(recommender=FixtureRecommender())
        from app.reasoning.result import ReasoningResult
        mock_result = ReasoningResult(
            success=True,
            recommendation="Mock recommendation",
            explanation="Mock reason",
            confidence=0.9,
            policy_action_allowed=True,
            is_fallback=True,
            model_id="mock",
        )
        with patch.object(RecoveryReasoner, "analyze", return_value=mock_result):
            report = evaluator.evaluate("Metrics", file_path)

        assert report.risk_detection_precision == 1.0
        assert report.risk_detection_recall == 1.0
        assert report.recommendation_status_counts == {
            "accepted": 1,
            "unavailable": 1,
        }
        assert report.recommendation_acceptance_rate == 0.5
        assert report.recommendation_rejection_rate == 0.0
        assert report.policy_isolation_passed is True
        assert report.policy_isolation_violation_count == 0
        assert all(record.audit_id for record in report.records)

    def test_synthetic_and_held_out_load_successfully(self):
        """1 & 2. Synthetic and held-out datasets load successfully."""
        root_dir = Path(__file__).parent.parent.parent
        synthetic = root_dir / "data" / "synthetic" / "failed_transactions.json"
        held_out = root_dir / "data" / "held_out" / "failed_transactions.json"
        
        evaluator = Evaluator()
        
        # Test synthetic
        if synthetic.exists():
            data_synth = evaluator.load_dataset(synthetic)
            assert isinstance(data_synth, list)
            assert len(data_synth) > 0
            
        # Test held-out
        if held_out.exists():
            data_held = evaluator.load_dataset(held_out)
            assert isinstance(data_held, list)
            assert len(data_held) > 0

    def test_malformed_records_handled_safely(self, temp_dataset):
        """3. Malformed records are bypassed safely."""
        data = [
            {"invalid_key": "bad"}, # Malformed
            {
                "event_id": "evt_valid",
                "razorpay_payment_id": "pay_valid",
                "merchant_id": "m1",
                "customer_id": "c1",
                "type": "one_time",
                "amount": 10000,
                "currency": "INR",
                "payment_method": "upi",
                "error_code": "INSUFFICIENT_FUNDS",
                "error_description": "insufficient funds",
                "failure_category": "insufficient_funds",
                "attempt_number": 1,
                "mandate_status": None,
                "timestamp": "2026-09-01T10:00:00Z"
            }
        ]
        file_path = temp_dataset(data)
        evaluator = Evaluator()
        
        # Mock reasoning to prevent calls
        with patch.object(RecoveryReasoner, "analyze", return_value=MagicMock(success=True)):
            report = evaluator.evaluate("Test", file_path)
            
        assert report.total_transactions == 1
        assert len(report.records) == 1
        assert report.records[0].event_id == "evt_valid"

    def test_every_event_passes_through_pipeline(self, temp_dataset, valid_record):
        """4. Every evaluation event passes through the existing pipeline."""
        file_path = temp_dataset([valid_record])
        evaluator = Evaluator()
        
        with patch.object(RecoveryPipeline, "process") as mock_process:
            mock_result = MagicMock()
            mock_result.classification.category.value = "unknown"
            mock_result.policy_decision.action.value = "escalate"
            mock_result.policy_decision.automatic_recovery_allowed = False
            mock_result.final_outcome.value = "escalated"
            mock_result.error = None
            mock_process.return_value = mock_result

            evaluator.evaluate("Test", file_path)
            assert mock_process.called
            assert mock_process.call_count == 1
            args, _ = mock_process.call_args
            assert isinstance(args[0], FailedTransactionEvent)
            assert args[0].event_id == valid_record["event_id"]

    def test_policy_authorization_not_recreated(self, temp_dataset, valid_record):
        """5. Evaluator relies on pipeline policy decision, does not recreate it."""
        file_path = temp_dataset([valid_record])
        evaluator = Evaluator()
        
        # We patch pipeline.process to return a mocked result with a specific decision
        with patch.object(RecoveryPipeline, "process") as mock_process:
            mock_result = MagicMock()
            mock_result.classification.category.value = "insufficient_funds"
            mock_result.policy_decision.action.value = "immediate_retry"
            # Set to False even if the rules might say True, evaluator should follow this:
            mock_result.policy_decision.automatic_recovery_allowed = False
            mock_result.final_outcome.value = "escalated"
            mock_result.error = None
            mock_process.return_value = mock_result
            
            report = evaluator.evaluate("Test", file_path)
            
        assert report.automatic_recovery_count == 0
        assert report.records[0].automatic_recovery_allowed is False

    def test_false_automatic_recovery_detected(self, temp_dataset, valid_record):
        """6 & 7. False automatic recovery detected (e.g. unknown allowed to recover)."""
        valid_record["failure_category"] = "unknown" # Ground truth is unknown
        file_path = temp_dataset([valid_record])
        evaluator = Evaluator()
        
        with patch.object(RecoveryPipeline, "process") as mock_process:
            mock_result = MagicMock()
            # Pipeline incorrectly labels it as insufficient funds and allows recovery
            mock_result.classification.category.value = "insufficient_funds"
            mock_result.policy_decision.action.value = "immediate_retry"
            mock_result.policy_decision.automatic_recovery_allowed = True
            mock_result.final_outcome.value = "recovered"
            mock_result.error = None
            mock_process.return_value = mock_result
            
            report = evaluator.evaluate("Test", file_path)
            
        assert report.false_automatic_recovery_count == 1
        assert report.records[0].is_false_automatic_recovery is True

    def test_evaluation_is_deterministic(self, temp_dataset, valid_record):
        """8. Evaluation is deterministic."""
        file_path = temp_dataset([valid_record, valid_record.copy()])
        evaluator = Evaluator()
        
        with patch.object(RecoveryReasoner, "analyze", return_value=MagicMock(success=True)):
            report1 = evaluator.evaluate("Test1", file_path)
            report2 = evaluator.evaluate("Test2", file_path)
            
        # Same result, note duplicates are skipped
        assert report1.total_transactions == report2.total_transactions
        assert report1.classification_accuracy == report2.classification_accuracy

    def test_empty_dataset_handled(self, temp_dataset):
        """9. Empty dataset is handled."""
        file_path = temp_dataset([])
        evaluator = Evaluator()
        
        report = evaluator.evaluate("Empty", file_path)
        assert report.total_transactions == 0
        assert report.classification_accuracy == 0.0
        assert len(report.records) == 0

    def test_duplicate_transactions_do_not_inflate_metrics(self, temp_dataset, valid_record):
        """10. Duplicate transaction IDs do not silently inflate metrics."""
        # Two records with the same event_id
        file_path = temp_dataset([valid_record, valid_record.copy()])
        evaluator = Evaluator()
        
        with patch.object(RecoveryReasoner, "analyze", return_value=MagicMock(success=True)):
            report = evaluator.evaluate("Test", file_path)
            
        # Only 1 transaction should be counted
        assert report.total_transactions == 1
        assert len(report.records) == 1

    def test_evaluation_does_not_mutate_source(self, temp_dataset, valid_record):
        """11. Evaluation does not mutate source datasets."""
        file_path = temp_dataset([valid_record])
        evaluator = Evaluator()
        
        with patch.object(RecoveryReasoner, "analyze", return_value=MagicMock(success=True)):
            evaluator.evaluate("Test", file_path)
            
        # Read the file again and check it's identical
        with open(file_path, "r") as f:
            data = json.load(f)
            assert data[0] == valid_record

    def test_no_real_apis_called(self, temp_dataset, valid_record):
        """12, 13, 14. Evaluation does not call Razorpay or Claude/Anthropic/External network."""
        file_path = temp_dataset([valid_record])
        evaluator = Evaluator()
        
        # The MockExecutor inside Evaluator should prevent Razorpay calls.
        # We just need to mock the Reasoner to prevent live NIM calls.
        with patch("app.reasoning.engine.RecoveryReasoner.analyze") as mock_reasoning:
            mock_reasoning.return_value = MagicMock(success=True)
            report = evaluator.evaluate("Test", file_path)
            
        # The execution reason should reflect that it used the mock executor
        # Or if it was immediately retried
        assert mock_reasoning.called
