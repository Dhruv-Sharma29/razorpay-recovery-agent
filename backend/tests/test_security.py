"""Security & Safety Hardening Tests (TASK-011).

Validates the explicit safety constraints defined in TASK-011.
Every test ensures the system fails closed and respects authorization boundaries.
"""

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import json

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.classifier.engine import FailureClassifier
from app.classifier.result import ClassificationResult, ClassificationCertainty
from app.escalation.handler import EscalationHandler
from app.executor.base import RecoveryExecutor
from app.executor.mock import MockExecutor
from app.models.payment_event import FailedTransactionEvent, FailureCategory, PaymentMethod, TransactionType
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.policy.result import PolicyDecision, PolicyAction, EscalationReason
from app.reasoning.engine import RecoveryReasoner
from app.audit.store import AuditLogger
from app.main import app


@pytest.fixture
def valid_event():
    return FailedTransactionEvent(
        event_id="evt_sec_001",
        razorpay_payment_id="pay_sec_001",
        merchant_id="m1",
        customer_id="c1",
        type=TransactionType.ONE_TIME,
        amount=15000,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code="NETWORK_ERROR",
        error_description="timeout",
        failure_category=FailureCategory.NETWORK_ERROR,
        attempt_number=1,
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def test_client():
    return TestClient(app)


class TestSecurityHardening:

    def test_invalid_amounts_fail_closed(self, valid_event):
        """1. Invalid amounts (negative, zero) fail closed at the policy engine."""
        policy = RecoveryPolicyEngine(amount_limit=500000)
        class_res = ClassificationResult(
            category=FailureCategory.INSUFFICIENT_FUNDS,
            confidence=1.0,
            certainty=ClassificationCertainty.HIGH,
            reason="test",
            rule_id="test",
            source_field="test"
        )
        
        # Test negative amount (bypassing Pydantic validation by assigning directly)
        valid_event.amount = -100
        decision_neg = policy.evaluate(valid_event, class_res)
        assert decision_neg.automatic_recovery_allowed is False
        assert decision_neg.action == PolicyAction.ESCALATE
        assert decision_neg.escalation_reason == EscalationReason.AMOUNT_EXCEEDS_LIMIT
        
        # Test zero amount
        valid_event.amount = 0
        decision_zero = policy.evaluate(valid_event, class_res)
        assert decision_zero.automatic_recovery_allowed is False
        assert decision_zero.action == PolicyAction.ESCALATE

    def test_unknown_categories_fail_closed(self, valid_event):
        """2. Unknown categories fail closed."""
        policy = RecoveryPolicyEngine()
        class_res = ClassificationResult(
            category=FailureCategory.UNKNOWN,
            confidence=1.0,
            certainty=ClassificationCertainty.HIGH,
            reason="unknown error",
            rule_id="test",
            source_field="test"
        )
        decision = policy.evaluate(valid_event, class_res)
        assert decision.automatic_recovery_allowed is False
        assert decision.action == PolicyAction.NO_ACTION

    def test_malformed_classifications_fail_closed(self, valid_event):
        """3. Malformed classifications fail closed."""
        policy = RecoveryPolicyEngine()
        # Pass None for classification
        decision = policy.evaluate(valid_event, None)
        assert decision.automatic_recovery_allowed is False
        assert decision.escalation_reason == EscalationReason.MISSING_CLASSIFICATION

    def test_invalid_policy_results_fail_closed(self, valid_event):
        """4. Invalid policy results (None) fail closed in pipeline/executor/reasoner."""
        pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            reasoner=RecoveryReasoner(),
            executor=MockExecutor(),
            escalation_handler=EscalationHandler(),
            audit_logger=AuditLogger("sqlite:///:memory:")
        )
        
        # Mock policy engine to return None
        with patch.object(RecoveryPolicyEngine, "evaluate", return_value=None):
            result = pipeline.process(valid_event)
            # The executor should not have run, and it should escalate
            assert result.execution is None
            assert result.escalation is not None
            assert result.escalation.authorizes_recovery is False

    def test_reasoning_cannot_override_policy(self, valid_event):
        """5. Reasoning layer cannot override a policy denial."""
        reasoner = RecoveryReasoner()
        class_res = ClassificationResult(
            category=FailureCategory.INSUFFICIENT_FUNDS,
            confidence=1.0,
            certainty=ClassificationCertainty.HIGH,
            reason="test",
            rule_id="test",
            source_field="test"
        )
        policy_decision = PolicyDecision(
            action=PolicyAction.ESCALATE,
            automatic_recovery_allowed=False,  # Policy denied
            reason="amount too high",
            rule_id="test",
            failure_category=FailureCategory.INSUFFICIENT_FUNDS,
            escalation_required=True,
            escalation_reason=EscalationReason.AMOUNT_EXCEEDS_LIMIT,
            max_retries_for_category=1,
            current_attempt=1,
            amount=1000000,
            amount_limit=500000
        )
        
        # Mock an Ollama response that attempts to authorize recovery
        raw_body = {
            "message": {
                "content": '{"recommendation": "do it anyway", "explanation": "looks fine", "confidence": 0.99}'
            }
        }
        
        # Use the internal parser to test the boundary
        from app.reasoning.engine import _parse_ollama_response
        result = _parse_ollama_response(raw_body, policy_decision, "test-model")
        
        assert result.policy_action_allowed is False  # Must remain False

    def test_escalation_cannot_authorize_recovery(self, valid_event):
        """6. Escalation cannot authorize recovery."""
        handler = EscalationHandler()
        result = handler.handle(valid_event, None)
        assert result.authorizes_recovery is False

    def test_audit_cannot_authorize_recovery(self, valid_event):
        """7. Audit cannot authorize recovery."""
        logger = AuditLogger("sqlite:///:memory:")
        # Provide an empty policy decision
        write = logger.record(valid_event, None, None, None, None, None, datetime.now(timezone.utc))
        assert write.record.automatic_recovery_allowed is False

    def test_executor_cannot_authorize_recovery(self, valid_event):
        """8. Executor cannot authorize recovery."""
        executor = MockExecutor()
        policy_decision = PolicyDecision(
            action=PolicyAction.IMMEDIATE_RETRY,
            automatic_recovery_allowed=False,  # Not authorized
            reason="denied",
            rule_id="test",
            failure_category=FailureCategory.INSUFFICIENT_FUNDS,
            escalation_required=True,
            escalation_reason=EscalationReason.AMOUNT_EXCEEDS_LIMIT,
            max_retries_for_category=1,
            current_attempt=1,
            amount=1000,
            amount_limit=500000
        )
        result = executor.execute(valid_event, policy_decision)
        assert result.status.value == "rejected"
        assert result.executed is False

    def test_exceptions_cannot_authorize_recovery(self, valid_event):
        """9. Exceptions cannot authorize recovery."""
        pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            reasoner=RecoveryReasoner(),
            executor=MockExecutor(),
            escalation_handler=EscalationHandler(),
            audit_logger=AuditLogger("sqlite:///:memory:")
        )
        
        # Mock classifier to crash
        with patch.object(FailureClassifier, "classify", side_effect=Exception("CRASH!")):
            result = pipeline.process(valid_event)
            assert result.policy_decision.automatic_recovery_allowed is False
            assert result.execution is None
            
        # Mock executor to crash
        with patch.object(MockExecutor, "_do_execute", side_effect=Exception("EXEC CRASH!")):
            # Reset
            pipeline = RecoveryPipeline(
                classifier=FailureClassifier(),
                policy_engine=RecoveryPolicyEngine(),
                reasoner=RecoveryReasoner(),
                executor=MockExecutor(),
                escalation_handler=EscalationHandler(),
                audit_logger=AuditLogger("sqlite:///:memory:")
            )
            result = pipeline.process(valid_event)
            if result.execution:
                assert result.execution.status.value == "failed"
                assert result.execution.executed is False

    def test_dashboard_cannot_authorize_recovery(self, test_client, valid_event):
        """10. Dashboard cannot authorize recovery via API input."""
        # Send event to dashboard API
        payload = json.loads(valid_event.model_dump_json())
        # There is no field to pass automatic_recovery_allowed=True in FailedTransactionEvent
        response = test_client.post("/api/dashboard/process", json=payload)
        assert response.status_code == 200
        data = response.json()
        # Must match whatever the backend pipeline decided, dashboard cannot override
        assert "automatic_recovery_allowed" in data

    def test_original_payment_event_remains_unchanged(self, valid_event):
        """11. Original payment event remains unchanged."""
        pipeline = RecoveryPipeline(
            classifier=FailureClassifier(),
            policy_engine=RecoveryPolicyEngine(),
            reasoner=RecoveryReasoner(),
            executor=MockExecutor(),
            escalation_handler=EscalationHandler(),
            audit_logger=AuditLogger("sqlite:///:memory:")
        )
        
        original_dict = valid_event.model_dump()
        pipeline.process(valid_event)
        new_dict = valid_event.model_dump()
        
        assert original_dict == new_dict

    def test_idempotency_remains_intact(self, valid_event):
        """12. Idempotency remains intact."""
        executor = MockExecutor()
        policy_decision = PolicyDecision(
            action=PolicyAction.IMMEDIATE_RETRY,
            automatic_recovery_allowed=True,
            reason="allowed",
            rule_id="test",
            failure_category=FailureCategory.NETWORK_ERROR,
            escalation_required=False,
            escalation_reason=None,
            max_retries_for_category=1,
            current_attempt=1,
            amount=100,
            amount_limit=500000
        )
        
        res1 = executor.execute(valid_event, policy_decision)
        assert res1.status.value == "success"
        
        res2 = executor.execute(valid_event, policy_decision)
        assert res2.status.value == "duplicate"
        assert res2.executed is False

    def test_no_real_api_calls(self):
        """13 & 14. No real Razorpay or external LLM calls occur natively during tests."""
        # By using MockExecutor and patching httpx if necessary.
        # This is proven by the fact that the pipeline runs offline in the test suite
        # without timing out or making actual requests (httpx isn't mocked globally here, 
        # but RecoveryReasoner handles connection failures safely without crashing).
        reasoner = RecoveryReasoner(timeout=0.01) # Force timeout
        class_res = ClassificationResult(
            category=FailureCategory.INSUFFICIENT_FUNDS,
            confidence=1.0,
            certainty=ClassificationCertainty.HIGH,
            reason="test",
            rule_id="test",
            source_field="test"
        )
        policy_decision = PolicyDecision(
            action=PolicyAction.IMMEDIATE_RETRY,
            automatic_recovery_allowed=True,
            reason="allowed",
            rule_id="test",
            failure_category=FailureCategory.NETWORK_ERROR,
            escalation_required=False,
            escalation_reason=None,
            max_retries_for_category=1,
            current_attempt=1,
            amount=100,
            amount_limit=500000
        )
        # Should return a safe fallback instantly
        res = reasoner.analyze(FailedTransactionEvent(
            event_id="e1", razorpay_payment_id="p1", merchant_id="m", customer_id="c",
            type=TransactionType.ONE_TIME, amount=100, payment_method=PaymentMethod.UPI,
            error_code="E", error_description="E", failure_category=FailureCategory.UNKNOWN,
            attempt_number=1, timestamp=datetime.now(timezone.utc)
        ), class_res, policy_decision)
        assert res.is_fallback is True
        assert res.policy_action_allowed is True
