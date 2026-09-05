"""Customer outreach for recovery actions.

Outreach never decides anything. It delivers a message the reasoning layer
drafted, for an action the policy already authorised.
"""

from app.outreach.dispatcher import (
    OutreachChannel,
    OutreachResult,
    SimulatedOutreachDispatcher,
)

__all__ = [
    "OutreachChannel",
    "OutreachResult",
    "SimulatedOutreachDispatcher",
]
