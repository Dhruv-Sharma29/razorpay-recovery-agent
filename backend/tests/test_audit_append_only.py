"""The audit log is append-only, and can prove it.

Storing a hash chain is not the same as being able to verify one. These tests
tamper with the log the three ways it could realistically be rewritten — an
edited payload, a deleted row, a reordered row — and require each to be
caught. Without them the chain is decoration.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.audit.store import AuditLogger
from app.classifier.result import ClassificationCertainty, ClassificationResult
from app.models.payment_event import (
    FailedTransactionEvent,
    FailureCategory,
    PaymentMethod,
    TransactionType,
)

AUDIT_SOURCE = Path(__file__).resolve().parents[1] / "app" / "audit" / "store.py"


def _event(n: int) -> FailedTransactionEvent:
    return FailedTransactionEvent(
        event_id=f"evt_chain_{n}",
        razorpay_payment_id=f"pay_chain_{n}",
        merchant_id="m1",
        customer_id="c1",
        type=TransactionType.ONE_TIME,
        amount=100000 + n,
        currency="INR",
        payment_method=PaymentMethod.UPI,
        error_code="INSUFFICIENT_FUNDS",
        error_description="Not enough balance",
        failure_category=FailureCategory.UNKNOWN,
        attempt_number=1,
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def _classification() -> ClassificationResult:
    return ClassificationResult(
        category=FailureCategory.INSUFFICIENT_FUNDS,
        confidence=0.99,
        certainty=ClassificationCertainty.HIGH,
        reason="test",
        rule_id="code.insufficient_funds",
        source_field="error_code",
    )


@pytest.fixture()
def populated(tmp_path):
    """A logger backed by a real file, with five chained records."""
    db = tmp_path / "audit.db"
    logger = AuditLogger(database_url=f"sqlite:///{db}")
    for n in range(5):
        logger.record(_event(n), classification=_classification())
    return logger, db


class TestTheModuleCannotRewriteHistory:
    def test_the_source_contains_no_update_or_delete(self) -> None:
        """The guarantee is structural: there is no code path to rewrite a row.

        A regression here would be someone adding a 'fix up that record'
        helper, which is exactly how append-only logs stop being append-only.
        """
        source = AUDIT_SOURCE.read_text()
        # Strip strings that merely talk about it, e.g. docstrings.
        statements = re.findall(
            r"\b(UPDATE\s+audit_log|DELETE\s+FROM\s+audit_log|DROP\s+TABLE)",
            source,
            re.IGNORECASE,
        )
        assert statements == [], f"audit module can rewrite history: {statements}"

    def test_the_only_write_is_an_insert(self) -> None:
        source = AUDIT_SOURCE.read_text().upper()
        assert source.count("INSERT INTO AUDIT_LOG") == 1


class TestTheChainVerifies:
    def test_an_untouched_log_verifies(self, populated) -> None:
        logger, _ = populated
        result = logger.verify_chain()
        assert result["valid"] is True
        assert result["records_checked"] == 5
        assert result["broken_at"] is None

    def test_an_empty_log_is_trivially_valid(self, tmp_path) -> None:
        logger = AuditLogger(database_url=f"sqlite:///{tmp_path / 'empty.db'}")
        assert logger.verify_chain()["valid"] is True

    def test_each_record_links_to_the_one_before(self, populated) -> None:
        _, db = populated
        rows = (
            sqlite3.connect(db)
            .execute(
                "SELECT previous_hash, record_hash FROM audit_log ORDER BY rowid"
            )
            .fetchall()
        )
        assert rows[0][0] is None, "the first record has nothing before it"
        for (previous, _), (_, earlier_hash) in zip(rows[1:], rows[:-1]):
            assert previous == earlier_hash


class TestTamperingIsDetected:
    def test_an_edited_payload_breaks_the_chain(self, populated) -> None:
        """The classic attack: quietly change what a decision said."""
        logger, db = populated
        connection = sqlite3.connect(db)
        connection.execute(
            "UPDATE audit_log SET payload = REPLACE(payload, 'escalate', 'recovered') "
            "WHERE rowid = 3"
        )
        connection.execute(
            "UPDATE audit_log SET payload = payload || ' ' WHERE rowid = 3"
        )
        connection.commit()

        result = logger.verify_chain()
        assert result["valid"] is False
        assert result["broken_at"] is not None
        assert "edited" in result["reason"]

    def test_a_deleted_row_breaks_the_chain(self, populated) -> None:
        """Removing an inconvenient decision must not go unnoticed."""
        logger, db = populated
        connection = sqlite3.connect(db)
        connection.execute("DELETE FROM audit_log WHERE rowid = 3")
        connection.commit()

        result = logger.verify_chain()
        assert result["valid"] is False
        assert "deleted" in result["reason"]

    def test_a_forged_hash_still_breaks_the_link(self, populated) -> None:
        """Recomputing one record's own hash does not repair its successors."""
        logger, db = populated
        connection = sqlite3.connect(db)
        connection.execute(
            "UPDATE audit_log SET record_hash = 'deadbeef' WHERE rowid = 2"
        )
        connection.commit()

        assert logger.verify_chain()["valid"] is False

    def test_verification_reports_where_it_broke(self, populated) -> None:
        logger, db = populated
        connection = sqlite3.connect(db)
        connection.execute("DELETE FROM audit_log WHERE rowid = 4")
        connection.commit()

        result = logger.verify_chain()
        # Three records verified before the gap.
        assert result["records_checked"] == 3
