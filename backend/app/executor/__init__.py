"""Recovery executor abstraction.

Provides the executor interface and mock implementation.  The Policy
Engine remains the sole authority — the executor only carries out
actions that the policy explicitly authorized.
"""

from app.executor.base import RecoveryExecutor, compute_idempotency_key
from app.executor.mock import MockExecutor
from app.executor.result import ExecutionResult, ExecutionStatus

__all__ = [
    "ExecutionResult",
    "ExecutionStatus",
    "MockExecutor",
    "RecoveryExecutor",
    "compute_idempotency_key",
]
