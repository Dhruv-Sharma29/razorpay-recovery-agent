"""Mock/sandbox recovery executor.

This executor simulates recovery actions without making any external
network calls.  It is used for testing, development, and demo purposes.

A future Razorpay adapter can replace this implementation by subclassing
``RecoveryExecutor`` and implementing ``_do_execute`` with real API calls,
without changing the policy layer or the executor contract.
"""

from __future__ import annotations

import logging

from app.executor.base import RecoveryExecutor
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyDecision

logger = logging.getLogger(__name__)


class MockExecutor(RecoveryExecutor):
    """Mock executor that simulates recovery actions.

    Does NOT make any external API calls, network requests, or
    side effects beyond updating the internal idempotency store.

    Behavior:
    - All authorized actions succeed by default.
    - ``simulate_failure`` can be set to True to simulate downstream
      failures for testing error handling.
    - ``simulate_failure_message`` controls the error message.
    - ``execution_log`` records all executed actions for verification.
    """

    def __init__(
        self,
        simulate_failure: bool = False,
        simulate_failure_message: str = "Simulated downstream failure",
    ) -> None:
        super().__init__()
        self.simulate_failure = simulate_failure
        self.simulate_failure_message = simulate_failure_message
        self.execution_log: list[dict] = []

    def _do_execute(
        self,
        payment_event: FailedTransactionEvent,
        policy_decision: PolicyDecision,
        execution_id: str,
    ) -> tuple[bool, str | None]:
        """Simulate executing a recovery action.

        Records the execution in ``execution_log`` and returns success
        or failure based on the ``simulate_failure`` flag.

        No external calls are made.
        """
        action = policy_decision.action.value

        logger.info(
            "MockExecutor: %s action=%s payment=%s execution_id=%s",
            "SIMULATING FAILURE" if self.simulate_failure else "executing",
            action,
            payment_event.razorpay_payment_id,
            execution_id,
        )

        self.execution_log.append(
            {
                "execution_id": execution_id,
                "event_id": payment_event.event_id,
                "payment_id": payment_event.razorpay_payment_id,
                "action": action,
                "simulated_failure": self.simulate_failure,
            }
        )

        if self.simulate_failure:
            return False, self.simulate_failure_message

        return True, None
