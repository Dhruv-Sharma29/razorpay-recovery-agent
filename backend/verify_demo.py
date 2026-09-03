"""Demo Scenarios Verification (TASK-012).

Verifies the 5 key integration demo scenarios exactly as specified.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

from app.classifier.engine import FailureClassifier
from app.escalation.handler import EscalationHandler
from app.executor.mock import MockExecutor
from app.models.payment_event import FailedTransactionEvent, FailureCategory, PaymentMethod, TransactionType
from app.pipeline.engine import RecoveryPipeline
from app.policy.engine import RecoveryPolicyEngine
from app.reasoning.engine import RecoveryReasoner
from app.audit.store import AuditLogger


def _make_event(
    event_id: str,
    amount: int,
    error_code: str,
    error_desc: str,
    attempt: int
) -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id=event_id,
        razorpay_payment_id=f"pay_{event_id}",
        merchant_id="m1",
        customer_id="c1",
        type=TransactionType.ONE_TIME,
        amount=amount,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code=error_code,
        error_description=error_desc,
        failure_category=FailureCategory.UNKNOWN,
        attempt_number=attempt,
        timestamp=datetime.now(timezone.utc),
    )


def verify_demo():
    print("Initializing pipeline...")
    executor = MockExecutor()
    pipeline = RecoveryPipeline(
        classifier=FailureClassifier(),
        policy_engine=RecoveryPolicyEngine(amount_limit=500000),
        reasoner=RecoveryReasoner(),
        executor=executor,
        escalation_handler=EscalationHandler(),
        audit_logger=AuditLogger("sqlite:///:memory:")
    )

    scenarios = []
    
    # A. Golden-path recoverable transaction (insufficient funds)
    event_A = _make_event("golden", 1000, "INSUFFICIENT_FUNDS", "insufficient funds", 1)
    res_A = pipeline.process(event_A)
    assert res_A.final_outcome.value == "recovered"
    assert res_A.execution.status.value == "success"
    assert res_A.policy_decision.automatic_recovery_allowed is True
    scenarios.append("A. Golden-path recoverable transaction: PASSED")

    # B. Retry limit exceeded
    event_B = _make_event("retry_limit", 1000, "INSUFFICIENT_FUNDS", "insufficient funds", 3)
    res_B = pipeline.process(event_B)
    assert res_B.final_outcome.value == "escalated"
    assert res_B.policy_decision.automatic_recovery_allowed is False
    assert res_B.execution is None
    scenarios.append("B. Retry limit exceeded: PASSED")

    # C. Unknown failure
    event_C = _make_event("unknown", 1000, "WEIRD_ERROR", "something strange happened", 1)
    res_C = pipeline.process(event_C)
    assert res_C.final_outcome.value == "escalated"
    assert res_C.classification.category.value == "unknown"
    assert res_C.policy_decision.automatic_recovery_allowed is False
    assert res_C.execution is None
    scenarios.append("C. Unknown failure: PASSED")

    # D. Amount above automatic-recovery limit
    event_D = _make_event("amount_cap", 1000000, "INSUFFICIENT_FUNDS", "insufficient funds", 1)
    res_D = pipeline.process(event_D)
    assert res_D.final_outcome.value == "escalated"
    assert res_D.policy_decision.automatic_recovery_allowed is False
    assert res_D.execution is None
    scenarios.append("D. Amount above automatic-recovery limit: PASSED")

    # E. Executor failure
    executor.simulate_failure = True
    event_E = _make_event("exec_fail", 1000, "INSUFFICIENT_FUNDS", "insufficient funds", 1)
    res_E = pipeline.process(event_E)
    assert res_E.final_outcome.value == "escalated"
    assert res_E.policy_decision.automatic_recovery_allowed is True
    assert res_E.execution.status.value == "failed"
    scenarios.append("E. Executor failure: PASSED")

    print("\nDEMO VERIFICATION RESULTS:")
    for s in scenarios:
        print(f"[OK] {s}")


if __name__ == "__main__":
    verify_demo()
