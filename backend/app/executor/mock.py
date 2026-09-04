"""Simulated payment executor.

This executor simulates recovery actions without making any external
network calls.  It is used for testing, development, and demo purposes.

Unlike a bare mock it reports a *simulated payment result* — a payment
status and the amount recovered — so the pipeline can measure money
moved. Every result carries ``simulated=True``: no real gateway is ever
contacted.

A future Razorpay adapter can replace this implementation by subclassing
``RecoveryExecutor`` and implementing ``_do_execute`` with real API calls,
without changing the policy layer or the executor contract.
"""

from __future__ import annotations

import logging

from app.executor.base import ExecOutcome, RecoveryExecutor
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyDecision

logger = logging.getLogger(__name__)


class SimulatedPaymentExecutor(RecoveryExecutor):
    """Executor that simulates recovery actions and their payment result.

    Does NOT make any external API calls, network requests, or
    side effects beyond updating the internal idempotency store.

    Behavior:
    - All authorized actions succeed by default, reporting the payment as
      ``captured`` and recovering the event's full amount.
    - ``simulate_failure`` can be set to True to simulate downstream
      failures for testing error handling; the payment is then ``failed``
      and nothing is recovered.
    - ``execution_log`` records all executed actions for verification.
    """

    def __init__(
        self,
        simulate_failure: bool = False,
        simulate_failure_message: str = "Simulated downstream failure",
        state_store=None,
    ) -> None:
        super().__init__(state_store=state_store)
        self.simulate_failure = simulate_failure
        self.simulate_failure_message = simulate_failure_message
        self.execution_log: list[dict] = []

    def _do_execute(
        self,
        payment_event: FailedTransactionEvent,
        policy_decision: PolicyDecision,
        execution_id: str,
    ) -> ExecOutcome:
        """Simulate executing a recovery action and its payment result.

        Records the execution in ``execution_log`` and returns success
        or failure based on the ``simulate_failure`` flag.

        No external calls are made.
        """
        action = policy_decision.action.value

        logger.info(
            "SimulatedPaymentExecutor: %s action=%s payment=%s execution_id=%s",
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
            return ExecOutcome(
                success=False,
                error=self.simulate_failure_message,
                payment_status="failed",
                amount_recovered=0,
            )

        # The recovery attempt captured the original failed amount. This is
        # a simulation, not a gateway confirmation — ExecutionResult.simulated
        # stays True to say so.
        return ExecOutcome(
            success=True,
            error=None,
            payment_status="captured",
            amount_recovered=payment_event.amount,
        )


# Backwards-compatible alias: the executor was previously named MockExecutor.
MockExecutor = SimulatedPaymentExecutor
