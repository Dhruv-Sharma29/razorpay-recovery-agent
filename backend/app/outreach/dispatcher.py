"""Simulated customer outreach.

Some recovery actions need the customer to do something — re-authorise a
mandate, complete an authentication prompt, pick another payment method.
Those are the only cases where contacting the customer is warranted; a
silent retry against the payment rail is not "contact" and must not be
counted as such.

Nothing here is a real send: every result carries ``simulated=True``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyAction

logger = logging.getLogger(__name__)


class OutreachChannel(str, Enum):
    """How the customer was reached."""

    EMAIL = "email"
    SMS = "sms"
    NONE = "none"


# Only these actions require the customer to act. Retries against the rail
# happen without the customer's involvement and are deliberately excluded.
_CONTACT_ACTIONS: frozenset[PolicyAction] = frozenset(
    {
        PolicyAction.TRIGGER_REAUTHORIZATION,
        PolicyAction.RESEND_AUTH_PROMPT,
        PolicyAction.SWITCH_PAYMENT_METHOD,
        PolicyAction.SEND_PAYMENT_REMINDER,
    }
)


class OutreachResult(BaseModel):
    """Structured record of a contact attempt."""

    attempted: bool = Field(
        ..., description="Whether contacting the customer was warranted at all"
    )
    delivered: bool = Field(default=False, description="Simulated delivery succeeded")
    channel: OutreachChannel = Field(default=OutreachChannel.NONE)
    reference: str | None = Field(default=None, description="Simulated send id")
    message: str | None = Field(
        default=None,
        description="The copy that was sent. None when nothing was sent.",
    )
    reason: str = Field(..., description="Why contact was or was not attempted")
    simulated: bool = Field(
        default=True,
        description="Honesty flag: no real email or SMS provider was called.",
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SimulatedOutreachDispatcher:
    """Records a contact attempt without calling any provider."""

    def __init__(self, channel: OutreachChannel = OutreachChannel.EMAIL) -> None:
        self._channel = channel
        self.sent_log: list[dict] = []

    def dispatch(
        self,
        payment_event: FailedTransactionEvent,
        action: PolicyAction,
        message: str | None,
    ) -> OutreachResult:
        """Send the drafted message, if this action warrants contact."""
        if action not in _CONTACT_ACTIONS:
            return OutreachResult(
                attempted=False,
                reason=(
                    f"Action '{action.value}' runs against the payment rail; "
                    "the customer is not involved"
                ),
            )

        if not message:
            # The reasoning layer withholds copy that names amounts or
            # promises an outcome. No draft means nothing safe to send.
            return OutreachResult(
                attempted=True,
                delivered=False,
                channel=self._channel,
                reason="No compliant message was available to send",
            )

        reference = f"out_{uuid.uuid4().hex[:12]}"
        self.sent_log.append(
            {
                "reference": reference,
                "payment_id": payment_event.razorpay_payment_id,
                "channel": self._channel.value,
            }
        )
        logger.info(
            "SimulatedOutreachDispatcher: %s to %s for %s",
            self._channel.value,
            payment_event.customer_id,
            payment_event.razorpay_payment_id,
        )
        return OutreachResult(
            attempted=True,
            delivered=True,
            channel=self._channel,
            reference=reference,
            message=message,
            reason=f"Recovery action '{action.value}' requires the customer to act",
        )
