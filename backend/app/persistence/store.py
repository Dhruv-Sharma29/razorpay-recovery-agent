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
import uuid
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

_CREATE_SCHEDULED_JOBS_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id           TEXT PRIMARY KEY,
    payment_id       TEXT NOT NULL,
    event_id         TEXT NOT NULL,
    action           TEXT NOT NULL,
    next_eligible_at TEXT NOT NULL,
    status           TEXT NOT NULL,
    event_json       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
)
"""

_CREATE_SCHEDULED_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_scheduled_due "
    "ON scheduled_jobs (status, next_eligible_at)"
)

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
        self._connection.execute(_CREATE_SCHEDULED_JOBS_SQL)
        self._connection.execute(_CREATE_SCHEDULED_INDEX_SQL)
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

    # --- Scheduled jobs (deferred retries) ------------------------------

    def schedule_job(
        self,
        *,
        payment_id: str,
        event_id: str,
        action: str,
        next_eligible_at: datetime,
        event_json: str,
        job_id: str | None = None,
    ) -> str:
        """Persist a deferred recovery action. Returns the job id.

        Idempotent per (payment_id, event_id, action): re-scheduling the
        same work returns the existing pending job instead of duplicating it.
        """
        existing = self._connection.execute(
            "SELECT job_id FROM scheduled_jobs "
            "WHERE payment_id = ? AND event_id = ? AND action = ? "
            "AND status = 'pending'",
            (payment_id, event_id, action),
        ).fetchone()
        if existing is not None:
            return existing[0]

        now = _now_iso()
        new_id = job_id or str(uuid.uuid4())
        self._connection.execute(
            "INSERT INTO scheduled_jobs (job_id, payment_id, event_id, action, "
            "next_eligible_at, status, event_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (
                new_id,
                payment_id,
                event_id,
                action,
                next_eligible_at.isoformat(),
                event_json,
                now,
                now,
            ),
        )
        self._connection.commit()
        return new_id

    def due_jobs(self, now: datetime) -> list[dict[str, Any]]:
        """Pending jobs whose cooldown has elapsed, oldest first."""
        rows = self._connection.execute(
            "SELECT job_id, payment_id, event_id, action, next_eligible_at, "
            "status, event_json, created_at, updated_at FROM scheduled_jobs "
            "WHERE status = 'pending' AND next_eligible_at <= ? "
            "ORDER BY next_eligible_at ASC",
            (now.isoformat(),),
        ).fetchall()
        return [self._job_row(r) for r in rows]

    def mark_job(self, job_id: str, status: str) -> None:
        """Move a job out of pending (``done`` or ``failed``)."""
        self._connection.execute(
            "UPDATE scheduled_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
            (status, _now_iso(), job_id),
        )
        self._connection.commit()

    def list_jobs(self, status: str | None = None) -> list[dict[str, Any]]:
        """All jobs, optionally filtered by status."""
        if status is None:
            rows = self._connection.execute(
                "SELECT job_id, payment_id, event_id, action, next_eligible_at, "
                "status, event_json, created_at, updated_at FROM scheduled_jobs "
                "ORDER BY created_at ASC"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT job_id, payment_id, event_id, action, next_eligible_at, "
                "status, event_json, created_at, updated_at FROM scheduled_jobs "
                "WHERE status = ? ORDER BY created_at ASC",
                (status,),
            ).fetchall()
        return [self._job_row(r) for r in rows]

    @staticmethod
    def _job_row(row: Any) -> dict[str, Any]:
        return {
            "job_id": row[0],
            "payment_id": row[1],
            "event_id": row[2],
            "action": row[3],
            "next_eligible_at": row[4],
            "status": row[5],
            "event_json": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }

    def clear(self) -> None:
        """Drop all idempotency, attempt, and scheduled-job state.

        Test-mode only: this deliberately discards the guarantees that stop
        a payment being retried twice, so it must never run in production.
        """
        self._connection.execute("DELETE FROM execution_ledger")
        self._connection.execute("DELETE FROM recovery_attempts")
        self._connection.execute("DELETE FROM scheduled_jobs")
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()
