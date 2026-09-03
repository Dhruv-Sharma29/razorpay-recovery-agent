"""Durable recovery-state store (stdlib sqlite3).

Persists two things the pipeline previously kept only in memory:

* an **execution ledger** — idempotency keys and their outcome, so a retry is
  never executed twice even across a process restart; and
* a **recovery-attempts history** — one row per attempt for a payment, so the
  server can derive the true attempt count from its own records instead of
  trusting a client-supplied number.

Like the audit store this uses ``settings.database_url`` and never calls
Razorpay, NIM, or mutates the payment event. It is append-and-upsert only.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def resolve_sqlite_path(database_url: str) -> str:
    """Map a SQLAlchemy-style sqlite URL to a sqlite3 path.

    Kept local (rather than imported from app.audit.store) to avoid a circular
    import: the executor imports this module, and app.audit.store transitively
    imports the executor.
    """
    url = (database_url or "").strip()
    if not url:
        return ":memory:"
    if url in {":memory:", "sqlite://", "sqlite:///:memory:"}:
        return ":memory:"
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        return ":memory:" if path == ":memory:" else path
    if url.startswith("sqlite://"):
        rest = url[len("sqlite://"):]
        if rest.startswith("/") and not rest.startswith("///"):
            return rest
        return rest or ":memory:"
    return url

_CREATE_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS execution_ledger (
    idempotency_key TEXT PRIMARY KEY,
    payment_id      TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    action          TEXT NOT NULL,
    status          TEXT NOT NULL,
    execution_id    TEXT,
    executed        INTEGER NOT NULL,
    recorded_at     TEXT NOT NULL
)
"""

_CREATE_ATTEMPTS_SQL = """
CREATE TABLE IF NOT EXISTS recovery_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id  TEXT NOT NULL,
    event_id    TEXT NOT NULL,
    action      TEXT,
    status      TEXT,
    created_at  TEXT NOT NULL
)
"""

_CREATE_ATTEMPTS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_attempts_payment "
    "ON recovery_attempts (payment_id)"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecoveryStateStore:
    """Durable idempotency + attempt-history store.

    Args:
        database_url: SQLite URL. Defaults to ``settings.database_url``.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = (
            database_url if database_url is not None else settings.database_url
        )
        self._path = resolve_sqlite_path(self._database_url)
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.execute(_CREATE_LEDGER_SQL)
        self._connection.execute(_CREATE_ATTEMPTS_SQL)
        self._connection.execute(_CREATE_ATTEMPTS_INDEX_SQL)
        self._connection.commit()

    @property
    def database_url(self) -> str:
        return self._database_url

    # --- Execution ledger (idempotency) ---------------------------------

    def get_execution(self, idempotency_key: str) -> dict[str, Any] | None:
        """Return the recorded execution for a key, or None if never seen."""
        row = self._connection.execute(
            "SELECT idempotency_key, payment_id, event_id, action, status, "
            "execution_id, executed, recorded_at FROM execution_ledger "
            "WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "idempotency_key": row[0],
            "payment_id": row[1],
            "event_id": row[2],
            "action": row[3],
            "status": row[4],
            "execution_id": row[5],
            "executed": bool(row[6]),
            "recorded_at": row[7],
        }

    def record_execution(
        self,
        *,
        idempotency_key: str,
        payment_id: str,
        event_id: str,
        action: str,
        status: str,
        execution_id: str | None,
        executed: bool,
    ) -> None:
        """Persist an execution outcome for the idempotency key.

        Uses INSERT OR IGNORE so the FIRST outcome for a key wins and later
        duplicates never overwrite it.
        """
        try:
            self._connection.execute(
                "INSERT OR IGNORE INTO execution_ledger "
                "(idempotency_key, payment_id, event_id, action, status, "
                "execution_id, executed, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    idempotency_key,
                    payment_id,
                    event_id,
                    action,
                    status,
                    execution_id,
                    1 if executed else 0,
                    _now_iso(),
                ),
            )
            self._connection.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ledger write failed: %s", exc)
            try:
                self._connection.rollback()
            except Exception:  # noqa: BLE001
                pass

    # --- Attempt history -------------------------------------------------

    def record_attempt(
        self,
        *,
        payment_id: str,
        event_id: str,
        action: str | None,
        status: str | None,
    ) -> None:
        """Append one attempt row for a payment."""
        try:
            self._connection.execute(
                "INSERT INTO recovery_attempts "
                "(payment_id, event_id, action, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (payment_id, event_id, action, status, _now_iso()),
            )
            self._connection.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Attempt write failed: %s", exc)
            try:
                self._connection.rollback()
            except Exception:  # noqa: BLE001
                pass

    def count_attempts(self, payment_id: str) -> int:
        """Number of recorded attempts for a payment (server-side truth)."""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM recovery_attempts WHERE payment_id = ?",
            (payment_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    def next_attempt_number(self, payment_id: str) -> int:
        """The attempt number a new attempt for this payment would take.

        Derived from history: one more than the count already recorded. This
        is the server-authoritative alternative to a client-supplied count.
        """
        return self.count_attempts(payment_id) + 1

    def close(self) -> None:
        self._connection.close()
