"""Advisory reasoning layer via NVIDIA NIM.

Provides structured explanations of recovery decisions that have already
been made by the deterministic policy engine.  The reasoning layer
CANNOT authorize, override, or change policy decisions.
"""

from app.reasoning.engine import RecoveryReasoner
from app.reasoning.result import ReasoningResult

__all__ = [
    "RecoveryReasoner",
    "ReasoningResult",
]
