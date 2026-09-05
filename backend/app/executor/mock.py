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

import hashlib

from app.classifier.result import FailureCategory
from app.executor.base import ExecOutcome, RecoveryExecutor
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyDecision

logger = logging.getLogger(__name__)

# How often a recovery action actually captures, by cause and action.
#
# This is a stated model, not measured production data: a retry into an
# account that is still empty mostly fails, whereas the same retry a day
# later mostly works. Without it every attempt would succeed and the
# reported recovery rate would be a meaningless 100%.
#
# Outcomes are deterministic per payment, so a seeded batch is reproducible.
_SUCCESS_RATES: dict[tuple[str, str], float] = {
    # Funds are the constraint: waiting is what makes this work.
    ("insufficient_funds", "immediate_retry"): 0.20,
    ("insufficient_funds", "scheduled_retry"): 0.70,
    # A transient gateway fault usually clears straight away.
    ("network_error", "immediate_retry"): 0.85,
    ("network_error", "scheduled_retry"): 0.80,
    # The issuer will decline again; changing instrument is what helps.
    ("bank_decline", "switch_payment_method"): 0.60,
    ("bank_decline", "scheduled_retry"): 0.20,
    # Needs the customer to act.
    ("authentication_failure", "resend_auth_prompt"): 0.65,
    ("authentication_failure", "scheduled_retry"): 0.15,
    ("expired_card", "trigger_reauthorization"): 0.55,
    ("expired_card", "switch_payment_method"): 0.45,
    # A chaser works, but not every time.
    ("overdue_receivable", "send_payment_reminder"): 0.40,
}

_DEFAULT_SUCCESS_RATE = 0.50


def _capture_succeeds(fingerprint: str, category: str, action: str) -> bool:
    """Deterministic for a given event *and* action.

    Keyed on the event's characteristics rather than its generated id, so
    the same logical event compared under two different actions differs
    only by the action — otherwise an A/B would be measuring luck.
    """
    rate = _SUCCESS_RATES.get((category, action), _DEFAULT_SUCCESS_RATE)
    digest = hashlib.sha256(f"{fingerprint}|{action}".encode("utf-8")).digest()
    # Uniform in [0, 1) from the first four bytes.
    draw = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return draw < rate


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
        realistic_capture: bool = False,
    ) -> None:
        super().__init__(state_store=state_store)
        self.simulate_failure = simulate_failure
        # When False every authorised action captures, which is convenient
        # for unit tests but reports an unbelievable 100% recovery rate.
        # The demo pipelines enable it.
        self.realistic_capture = realistic_capture
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

        # Whether the capture lands depends on the cause and the action
        # chosen for it — which is what makes choosing the action matter.
        # The classifier's verdict, carried on the policy decision. The
        # event's own failure_category is self-reported and never trusted.
        category = getattr(
            policy_decision.failure_category,
            "value",
            policy_decision.failure_category,
        )
        # Stable across runs of the same logical event, unlike the
        # per-run generated payment id.
        fingerprint = (
            f"{payment_event.customer_id}|{payment_event.amount}"
            f"|{payment_event.attempt_number}|{category}"
        )
        if self.realistic_capture and not _capture_succeeds(
            fingerprint, str(category), action
        ):
            return ExecOutcome(
                success=False,
                error=f"Simulated capture did not succeed for {action}",
                payment_status="failed",
                amount_recovered=0,
            )

        # This is a simulation, not a gateway confirmation —
        # ExecutionResult.simulated stays True to say so.
        return ExecOutcome(
            success=True,
            error=None,
            payment_status="captured",
            amount_recovered=payment_event.amount,
        )


# Backwards-compatible alias: the executor was previously named MockExecutor.
MockExecutor = SimulatedPaymentExecutor
