"""Append-only audit log for recovery-agent decisions.

Uses SQLite via ``Settings.database_url`` (stdlib ``sqlite3`` only).
The public API inserts rows; it does not update or delete them.

This component NEVER:
- authorizes recovery
- executes payments
- calls Razorpay
- calls NIM / Nemotron / external LLMs
- mutates the payment event or policy decision
- stores credentials or API keys
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.audit.result import AuditOutcome, AuditRecord, AuditWriteResult
from app.classifier.result import ClassificationResult
from app.config import settings
from app.escalation.result import EscalationResult, EscalationStatus
from app.executor.result import ExecutionResult, ExecutionStatus
from app.models.payment_event import FailedTransactionEvent
from app.policy.result import PolicyDecision
from app.recommendation.result import ApprovedPaymentHistory, RecoveryRecommendation
from app.reasoning.result import ReasoningResult

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payment_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    previous_hash TEXT,
    record_hash TEXT NOT NULL
)
"""

_INSERT_SQL = """
INSERT INTO audit_log (audit_id, recorded_at, event_id, payment_id, payload, previous_hash, record_hash)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_SECRET_KEY_FRAGMENTS = (
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "private_key",
    "razorpay_key",
    "key_id",
    "key_secret",
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"rzp_(live|test)_[A-Za-z0-9]+", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S+"),
)

_REDACTED = "[REDACTED]"


def resolve_sqlite_path(database_url: str) -> str:
    """Map a SQLAlchemy-style sqlite URL to a sqlite3 path."""
    url = (database_url or "").strip()
    if not url:
        return ":memory:"
    if url in {":memory:", "sqlite://", "sqlite:///:memory:"}:
        return ":memory:"
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix) :]
        if path == ":memory:":
            return ":memory:"
        return path
    if url.startswith("sqlite://"):
        rest = url[len("sqlite://") :]
        if rest.startswith("/") and not rest.startswith("///"):
            return rest
        return rest or ":memory:"
    return url


def _looks_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)


def redact_secrets(value: Any, key: str | None = None) -> Any:
    """Recursively strip credentials from structures before persistence."""
    if key is not None and _looks_secret_key(key):
        return _REDACTED
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_VALUE_PATTERNS:
            redacted = pattern.sub(_REDACTED, redacted)
        return redacted
    if isinstance(value, dict):
        return {k: redact_secrets(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


# Domain separator. This makes the reference stable across restarts so
# repeat failures can be grouped, while keeping the raw id out of storage.
# It is a pseudonym, not an anonymisation: the customer id space is small,
# so treat customer_ref as sensitive, just less so than the id itself.
_CUSTOMER_REF_SALT = "reflow.audit.customer_ref.v1"


def _pseudonymise(customer_id: str | None) -> str | None:
    """Stable, non-reversible-at-a-glance reference for a customer."""
    if not customer_id:
        return None
    digest = hashlib.sha256(
        f"{_CUSTOMER_REF_SALT}:{customer_id}".encode("utf-8")
    ).hexdigest()
    return f"cref_{digest[:16]}"


def _scheduled_for(execution: ExecutionResult) -> str | None:
    """ISO timestamp a deferred retry becomes eligible, if it was scheduled.

    The pipeline encodes it in the reason string when it defers; anything
    else has no scheduled time.
    """
    if execution.status != ExecutionStatus.SCHEDULED:
        return None
    reason = execution.reason or ""
    marker = "Scheduled for "
    if marker not in reason:
        return None
    return reason.split(marker, 1)[1].split(" ", 1)[0] or None


def derive_outcome(
    *,
    policy_decision: PolicyDecision | None,
    execution: ExecutionResult | None,
    escalation: EscalationResult | None,
) -> AuditOutcome:
    """Derive a final outcome without granting recovery authority."""
    if escalation is not None and escalation.status in (
        EscalationStatus.OPEN,
        EscalationStatus.FAILED_CLOSED,
    ):
        return AuditOutcome.ESCALATED
    if execution is not None and execution.status == ExecutionStatus.SCHEDULED:
        # Authorized but not yet run: pending, never "recovered".
        return AuditOutcome.PENDING
    if execution is not None and execution.status == ExecutionStatus.FAILED:
        return AuditOutcome.EXECUTION_FAILED
    if execution is not None and execution.status == ExecutionStatus.SUCCESS and execution.executed:
        return AuditOutcome.RECOVERED
    if policy_decision is not None and not policy_decision.automatic_recovery_allowed:
        return AuditOutcome.DENIED
    return AuditOutcome.RECORDED


class AuditLogger:
    """Append-only SQLite audit logger.

    Args:
        database_url: SQLite URL. Defaults to ``settings.database_url``.
    """

    def __init__(self, database_url: str | None = None) -> None:
        import threading
        self._database_url = (
            database_url if database_url is not None else settings.database_url
        )
        self._path = resolve_sqlite_path(self._database_url)
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        self._connection.execute(_CREATE_TABLE_SQL)
        self._connection.commit()

    @property
    def database_url(self) -> str:
        return self._database_url

    def record(
        self,
        payment_event: FailedTransactionEvent | None,
        *,
        classification: ClassificationResult | None = None,
        recommendation: RecoveryRecommendation | None = None,
        policy_decision: PolicyDecision | None = None,
        reasoning: ReasoningResult | None = None,
        execution: ExecutionResult | None = None,
        escalation: EscalationResult | None = None,
        timestamp: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AuditWriteResult:
        """Append one audit event.

        Failures return ``recorded=False`` and never authorize recovery.
        The original payment event and policy decision are not mutated.
        """
        now = timestamp if timestamp is not None else datetime.now(timezone.utc)
        audit_id = str(uuid.uuid4())

        try:
            record = self._build_record(
                audit_id=audit_id,
                now=now,
                payment_event=payment_event,
                classification=classification,
                recommendation=recommendation,
                policy_decision=policy_decision,
                reasoning=reasoning,
                execution=execution,
                escalation=escalation,
                extra=extra,
            )
            payload = redact_secrets(record.model_dump(mode="json"))
            payload_str = json.dumps(payload, sort_keys=True)

            with self._lock:
                cursor = self._connection.execute(
                    "SELECT record_hash FROM audit_log ORDER BY rowid DESC LIMIT 1"
                )
                row = cursor.fetchone()
                previous_hash = row[0] if row else None

                # Compute current hash
                hasher = hashlib.sha256()
                if previous_hash:
                    hasher.update(previous_hash.encode("utf-8"))
                hasher.update(payload_str.encode("utf-8"))
                record_hash = hasher.hexdigest()

                self._connection.execute(
                    _INSERT_SQL,
                    (
                        record.audit_id,
                        record.timestamp.isoformat(),
                        record.event_id,
                        record.payment_id,
                        payload_str,
                        previous_hash,
                        record_hash,
                    ),
                )
                self._connection.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Audit append failed: %s", exc)
            try:
                self._connection.rollback()
            except Exception:  # noqa: BLE001
                pass
            return AuditWriteResult(
                recorded=False,
                audit_id=None,
                error=str(exc),
                authorizes_recovery=False,
                record=None,
            )

        return AuditWriteResult(
            recorded=True,
            audit_id=record.audit_id,
            error=None,
            authorizes_recovery=False,
            record=record,
        )

    def list_records(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        outcome: str | None = None,
    ) -> list[AuditRecord]:
        """Return audit rows in append order (oldest first).

        Args:
            limit: Maximum rows to return. ``None`` returns all (default,
                preserving the original behavior).
            offset: Number of rows to skip, for pagination.
            outcome: Optional ``final_outcome`` filter (e.g. "recovered").
        """
        sql = "SELECT payload FROM audit_log"
        params: list[Any] = []
        if outcome is not None:
            sql += " WHERE json_extract(payload, '$.final_outcome') = ?"
            params.append(outcome)
        sql += " ORDER BY rowid ASC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        elif offset:
            # SQLite requires a LIMIT before OFFSET; -1 means "all remaining".
            sql += " LIMIT -1 OFFSET ?"
            params.append(int(offset))

        cursor = self._connection.execute(sql, params)
        records: list[AuditRecord] = []
        for (payload,) in cursor.fetchall():
            data = json.loads(payload)
            records.append(AuditRecord.model_validate(data))
        return records

    def count_records(self, *, outcome: str | None = None) -> int:
        """Return the total number of audit rows (optionally filtered)."""
        sql = "SELECT COUNT(*) FROM audit_log"
        params: list[Any] = []
        if outcome is not None:
            sql += " WHERE json_extract(payload, '$.final_outcome') = ?"
            params.append(outcome)
        row = self._connection.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def get_approved_payment_history(
        self, customer_id: str, *, limit: int = 5
    ) -> ApprovedPaymentHistory:
        """Return a redacted aggregate history suitable for model context.

        The audit log stores only a stable pseudonymous customer reference.
        This method never returns the underlying rows or customer identifier;
        it exposes bounded counts and recent outcome/category values only.
        """
        customer_ref = _pseudonymise(customer_id)
        if not customer_ref:
            return ApprovedPaymentHistory()

        safe_limit = max(1, min(int(limit), 20))
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM audit_log "
                "WHERE json_extract(payload, '$.customer_ref') = ? "
                "ORDER BY rowid DESC LIMIT ?",
                (customer_ref, safe_limit),
            ).fetchall()

        payloads = [json.loads(row[0]) for row in rows]
        recovered_amount = sum(
            int(payload.get("amount_recovered") or 0)
            for payload in payloads
            if isinstance(payload.get("amount_recovered"), (int, float))
        )
        outcomes = [
            str(payload.get("final_outcome"))
            for payload in payloads
            if payload.get("final_outcome")
        ]
        recovery_outcomes = {"recovered", "pending", "execution_failed"}
        return ApprovedPaymentHistory(
            prior_event_count=len(payloads),
            successful_payment_count=sum(
                1 for outcome in outcomes if outcome == "recovered"
            ),
            failed_payment_count=sum(
                1 for outcome in outcomes if outcome != "recovered"
            ),
            recovered_amount=max(0, recovered_amount),
            prior_recovery_attempts=sum(
                1 for outcome in outcomes if outcome in recovery_outcomes
            ),
            last_outcome=outcomes[0] if outcomes else None,
            last_failure_category=(
                payloads[0].get("classification_category")
                if payloads
                else None
            ),
            recent_outcomes=outcomes[:5],
        )

    def export_csv(self) -> str:
        """Export the full audit log as a CSV string."""
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(["rowid", "audit_id", "recorded_at", "event_id", "payment_id", "previous_hash", "record_hash", "final_outcome"])

        cursor = self._connection.execute(
            "SELECT rowid, audit_id, recorded_at, event_id, payment_id, previous_hash, record_hash, json_extract(payload, '$.final_outcome') "
            "FROM audit_log ORDER BY rowid ASC"
        )

        for row in cursor.fetchall():
            writer.writerow(row)

        return output.getvalue()

    def close(self) -> None:
        self._connection.close()

    def _build_record(
        self,
        *,
        audit_id: str,
        now: datetime,
        payment_event: FailedTransactionEvent | None,
        classification: ClassificationResult | None,
        recommendation: RecoveryRecommendation | None,
        policy_decision: PolicyDecision | None,
        reasoning: ReasoningResult | None,
        execution: ExecutionResult | None,
        escalation: EscalationResult | None,
        extra: dict[str, Any] | None,
    ) -> AuditRecord:
        event_id = "unknown"
        payment_id = "unknown"
        attempt_number = None
        amount = None
        merchant_id = None
        customer_ref = None
        transaction_type = None
        mandate_status = None
        if isinstance(payment_event, FailedTransactionEvent):
            event_id = payment_event.event_id
            payment_id = payment_event.razorpay_payment_id
            attempt_number = payment_event.attempt_number
            amount = payment_event.amount
            merchant_id = payment_event.merchant_id
            customer_ref = _pseudonymise(payment_event.customer_id)
            transaction_type = getattr(
                payment_event.type, "value", payment_event.type
            )
            mandate_status = getattr(
                payment_event.mandate_status, "value", payment_event.mandate_status
            )

        error_parts: list[str] = []
        if execution is not None and execution.error:
            error_parts.append(execution.error)
        if reasoning is not None and reasoning.error:
            error_parts.append(reasoning.error)
        if extra:
            extra_error = extra.get("error")
            if extra_error:
                error_parts.append(str(extra_error))

        error = redact_secrets("; ".join(error_parts)) if error_parts else None
        if isinstance(error, str) and not error:
            error = None

        return AuditRecord(
            audit_id=audit_id,
            event_id=event_id,
            payment_id=payment_id,
            timestamp=now,
            classification_category=(
                classification.category.value if classification is not None else None
            ),
            classification_reason=(
                classification.reason if classification is not None else None
            ),
            recommendation_success=(
                recommendation.success if recommendation is not None else None
            ),
            recommendation_model=(
                recommendation.model_id if recommendation is not None else None
            ),
            recommendation_latency_ms=(
                recommendation.latency_ms if recommendation is not None else None
            ),
            recommendation_prompt_version=(
                recommendation.prompt_version if recommendation is not None else None
            ),
            recommendation_revenue_at_risk=(
                recommendation.revenue_at_risk if recommendation is not None else None
            ),
            recommendation_risk_score=(
                recommendation.risk_score if recommendation is not None else None
            ),
            recommendation_suggested_cause=(
                recommendation.suggested_cause.value
                if recommendation is not None and recommendation.suggested_cause is not None
                else None
            ),
            recommendation_suggested_action=(
                recommendation.suggested_action.value
                if recommendation is not None and recommendation.suggested_action is not None
                else None
            ),
            recommendation_confidence=(
                recommendation.confidence if recommendation is not None else None
            ),
            recommendation_evidence=(
                recommendation.evidence if recommendation is not None else []
            ),
            recommendation_status=(
                policy_decision.recommendation_status
                if policy_decision is not None
                else None
            ),
            recommendation_is_fallback=(
                recommendation.is_fallback if recommendation is not None else None
            ),
            recommendation_fallback_reason=(
                recommendation.fallback_reason.value
                if recommendation is not None and recommendation.fallback_reason is not None
                else None
            ),
            policy_action=(
                policy_decision.action.value if policy_decision is not None else None
            ),
            policy_reason=(
                policy_decision.reason if policy_decision is not None else None
            ),
            automatic_recovery_allowed=(
                policy_decision.automatic_recovery_allowed
                if policy_decision is not None
                else None
            ),
            reasoning_success=(
                reasoning.success if reasoning is not None else None
            ),
            reasoning_reference=(
                reasoning.model_id if reasoning is not None else None
            ),
            execution_status=(
                execution.status.value if execution is not None else None
            ),
            execution_reference=(
                execution.execution_id if execution is not None else None
            ),
            escalation_status=(
                escalation.status.value if escalation is not None else None
            ),
            escalation_reference=(
                escalation.escalation_id if escalation is not None else None
            ),
            final_outcome=derive_outcome(
                policy_decision=policy_decision,
                execution=execution,
                escalation=escalation,
            ),
            error=error if isinstance(error, str) or error is None else str(error),
            attempt_number=attempt_number,
            amount=amount,
            merchant_id=merchant_id,
            customer_ref=customer_ref,
            transaction_type=transaction_type,
            mandate_status=mandate_status,
            # --- Decision chain ---
            classification_rule_id=(
                classification.rule_id if classification is not None else None
            ),
            policy_rule_id=(
                policy_decision.rule_id if policy_decision is not None else None
            ),
            amount_limit=(
                policy_decision.amount_limit if policy_decision is not None else None
            ),
            max_retries=(
                policy_decision.max_retries_for_category
                if policy_decision is not None
                else None
            ),
            cooldown_seconds=(
                getattr(policy_decision, "cooldown_seconds", None)
                if policy_decision is not None
                else None
            ),
            scheduled_for=(
                _scheduled_for(execution) if execution is not None else None
            ),
            payment_status=(
                execution.payment_status if execution is not None else None
            ),
            amount_recovered=(
                execution.amount_recovered if execution is not None else None
            ),
            escalation_trigger=(
                escalation.trigger.value
                if escalation is not None and escalation.trigger is not None
                else None
            ),
            reasoning_is_fallback=(
                reasoning.is_fallback if reasoning is not None else None
            ),
        )
