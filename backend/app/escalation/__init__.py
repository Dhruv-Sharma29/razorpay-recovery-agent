"""Escalation handler for cases that cannot be auto-recovered.

The Policy Engine remains the sole authority for recovery decisions.
This package only records that human intervention is required.
"""

from app.escalation.handler import EscalationHandler
from app.escalation.result import (
    EscalationResult,
    EscalationSeverity,
    EscalationStatus,
    EscalationTrigger,
)

__all__ = [
    "EscalationHandler",
    "EscalationResult",
    "EscalationSeverity",
    "EscalationStatus",
    "EscalationTrigger",
]
