"""Dashboard API tests (TASK-009).

Tests the dashboard-facing endpoints. No real Razorpay or NIM calls.
Validates that the API layer faithfully projects pipeline results
without duplicating or overriding policy logic.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event_payload(
    event_id: str = "evt_dash_001",
    payment_id: str = "pay_dash_001",
    amount: int = 149900,
    attempt: int = 1,
    error_code: str = "INSUFFICIENT_FUNDS",
    error_description: str = "Payment failed due to insufficient funds",
    failure_category: str = "unknown",
    payment_method: str = "upi",
    transaction_type: str = "one_time",
) -> dict:
    return {
        "event_id": event_id,
        "razorpay_payment_id": payment_id,
        "merchant_id": "merch_01",
        "customer_id": "cust_01",
        "type": transaction_type,
        "amount": amount,
        "currency": "INR",
        "payment_method": payment_method,
        "error_code": error_code,
        "error_description": error_description,
        "failure_category": failure_category,
        "attempt_number": attempt,
        "mandate_status": None,
        "timestamp": "2026-09-01T10:00:00Z",
    }


# We need to mock the reasoner so NIM is not called
@pytest.fixture(autouse=True)
def mock_reasoner():
    """Mock the NIM (Nemotron) reasoning call for all dashboard tests."""
    from app.reasoning.engine import RecoveryReasoner
    from app.reasoning.result import ReasoningResult

    def fake_analyze(self, payment_event, classification, policy_decision):
        return ReasoningResult(
            success=True,
            recommendation="Retry recommended by reasoning layer",
            explanation="The policy decision has been analyzed.",
            confidence=0.9,
            model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            policy_action_allowed=(
                policy_decision.automatic_recovery_allowed
                if policy_decision
                else False
            ),
            is_fallback=False,
            error=None,
        )

    with patch.object(RecoveryReasoner, "analyze", fake_analyze):
        yield


# ---------------------------------------------------------------------------
# Test 1: Successful recovery result
# ---------------------------------------------------------------------------


class TestDashboardProcess:
    def test_successful_recovery(self):
        """1. Insufficient funds, attempt 1 -> scheduled, so pending.

        Migrated for P2: the policy prescribes a 24h cooldown, so the retry
        is scheduled rather than executed inline. Reporting this as
        "recovered" would claim money moved before any retry ran. It becomes
        recovered once the scheduler worker runs (see
        test_scheduled_retry_recovers_after_worker_runs).
        """
        payload = _make_event_payload(
            error_code="INSUFFICIENT_FUNDS",
            amount=149900,
            attempt=1,
        )
        resp = client.post("/api/dashboard/process", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert data["payment_id"] == "pay_dash_001"
        assert data["event_id"] == "evt_dash_001"
        assert data["failure_category"] == "insufficient_funds"
        assert data["policy_action"] == "scheduled_retry"
        assert data["automatic_recovery_allowed"] is True
        assert data["execution_status"] == "scheduled"
        assert data["final_outcome"] == "pending"
        assert data["amount_recovered"] == 0
        assert data["amount"] == 149900
        assert data["attempt_number"] == 1

    def test_denied_recovery_retry_limit(self):
        """2. Bank decline attempt 2 → retry limit exhausted → denied/escalated."""
        payload = _make_event_payload(
            event_id="evt_dash_002",
            payment_id="pay_dash_002",
            error_code="BANK_DECLINED",
            attempt=2,
        )
        resp = client.post("/api/dashboard/process", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert data["failure_category"] == "bank_decline"
        assert data["automatic_recovery_allowed"] is False
        assert data["execution_status"] is None
        assert data["final_outcome"] == "escalated"

    def test_escalated_unknown_failure(self):
        """3. Unknown failure → always escalated."""
        payload = _make_event_payload(
            event_id="evt_dash_003",
            payment_id="pay_dash_003",
            error_code="SOMETHING_WEIRD",
            error_description="Something completely unrecognizable",
        )
        resp = client.post("/api/dashboard/process", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert data["failure_category"] == "unknown"
        assert data["automatic_recovery_allowed"] is False
        assert data["escalation_status"] == "open"
        assert data["final_outcome"] == "escalated"

    def test_failed_execution(self):
        """4. Executor failure → execution_failed or escalated.

        Uses GATEWAY_ERROR (immediate retry, no cooldown) so the action
        actually reaches the executor; an insufficient-funds retry is now
        deferred and would never fail inline.
        """
        # We need to make the mock executor fail
        from app.dashboard import _pipeline

        _pipeline.executor.simulate_failure = True
        _pipeline.executor.simulate_failure_message = "Downstream error"

        try:
            payload = _make_event_payload(
                event_id="evt_dash_004",
                payment_id="pay_dash_004",
                error_code="GATEWAY_ERROR",
                amount=149900,
                attempt=1,
            )
            resp = client.post("/api/dashboard/process", json=payload)
            assert resp.status_code == 200

            data = resp.json()
            assert data["execution_status"] == "failed"
            assert data["final_outcome"] in ("escalated", "execution_failed")
        finally:
            _pipeline.executor.simulate_failure = False
            _pipeline.executor.simulate_failure_message = None

    def test_unknown_unsafe_amount_exceeds_limit(self):
        """5. Amount exceeds auto-recovery limit → escalated (unsafe)."""
        payload = _make_event_payload(
            event_id="evt_dash_005",
            payment_id="pay_dash_005",
            amount=600000,  # Exceeds default 500000 limit
        )
        resp = client.post("/api/dashboard/process", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert data["automatic_recovery_allowed"] is False
        assert data["escalation_status"] == "open"
        assert data["final_outcome"] == "escalated"
        # Must never say "recovered"
        assert data["final_outcome"] != "recovered"

    def test_malformed_input(self):
        """6. Malformed input → 422 validation error."""
        payload = {"event_id": "broken"}  # Missing required fields
        resp = client.post("/api/dashboard/process", json=payload)
        assert resp.status_code == 422

    def test_malformed_input_bad_amount(self):
        """6b. Invalid amount → 422."""
        payload = _make_event_payload()
        payload["amount"] = -1  # Invalid: must be >= 100
        resp = client.post("/api/dashboard/process", json=payload)
        assert resp.status_code == 422

    def test_pipeline_error(self):
        """7. Pipeline exception → 500 error with detail."""
        from app.dashboard import _pipeline
        from app.pipeline.engine import RecoveryPipeline

        original = RecoveryPipeline.process

        def exploding_process(self, event):
            raise RuntimeError("Catastrophic pipeline failure")

        with patch.object(RecoveryPipeline, "process", exploding_process):
            payload = _make_event_payload(
                event_id="evt_dash_007",
                payment_id="pay_dash_007",
            )
            resp = client.post("/api/dashboard/process", json=payload)
            assert resp.status_code == 500
            assert "Pipeline error" in resp.json()["detail"]

    def test_policy_decision_exact_projection(self):
        """8. Policy decision is represented exactly as returned by pipeline."""
        payload = _make_event_payload(
            event_id="evt_dash_008",
            payment_id="pay_dash_008",
            error_code="GATEWAY_ERROR",
            attempt=1,
        )
        resp = client.post("/api/dashboard/process", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        # Network error → immediate_retry
        assert data["failure_category"] == "network_error"
        assert data["policy_action"] == "immediate_retry"
        assert data["automatic_recovery_allowed"] is True
        assert data["policy_reason"] is not None
        assert len(data["policy_reason"]) > 0

    def test_api_cannot_override_policy(self):
        """9. Frontend/API layer cannot override policy — attempt > global cap → still denied."""
        payload = _make_event_payload(
            event_id="evt_dash_009",
            payment_id="pay_dash_009",
            attempt=4,  # Exceeds global cap of 3
        )
        resp = client.post("/api/dashboard/process", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert data["automatic_recovery_allowed"] is False
        assert data["execution_status"] is None
        assert data["final_outcome"] != "recovered"


    def test_run_golden_path(self):
        """10. Golden path endpoint runs and returns successfully."""
        resp = client.post("/api/dashboard/golden-path")
        assert resp.status_code == 200

        data = resp.json()
        assert data["automatic_recovery_allowed"] is True
        assert data["final_outcome"] in ["pending", "recovered"]
        assert data["failure_category"] == "insufficient_funds"

# ---------------------------------------------------------------------------
# Audit endpoint tests
# ---------------------------------------------------------------------------


class TestDashboardAudit:
    def test_audit_log_returns_list(self):
        """Audit log endpoint returns a list of records."""
        resp = client.get("/api/dashboard/audit")
        assert resp.status_code == 200

        data = resp.json()
        assert "records" in data
        assert "count" in data
        assert isinstance(data["records"], list)
        assert data["count"] == len(data["records"])

    def test_audit_log_populates_after_process(self):
        """Audit log gains entries after processing events."""
        # Process an event first
        payload = _make_event_payload(
            event_id="evt_audit_001",
            payment_id="pay_audit_001",
        )
        client.post("/api/dashboard/process", json=payload)

        resp = client.get("/api/dashboard/audit")
        assert resp.status_code == 200

        data = resp.json()
        assert data["count"] >= 1
