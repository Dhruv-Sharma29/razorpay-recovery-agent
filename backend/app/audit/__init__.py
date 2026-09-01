"""Append-only audit log for recovery-agent decisions.

Audit records decisions. It never authorizes recovery or executes payments.
"""

from app.audit.result import AuditOutcome, AuditRecord, AuditWriteResult
from app.audit.store import AuditLogger, redact_secrets, resolve_sqlite_path

__all__ = [
    "AuditLogger",
    "AuditOutcome",
    "AuditRecord",
    "AuditWriteResult",
    "redact_secrets",
    "resolve_sqlite_path",
]
