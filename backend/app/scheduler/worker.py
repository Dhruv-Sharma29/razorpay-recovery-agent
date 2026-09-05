"""Scheduler worker: run recovery actions whose cooldown has elapsed.

The worker does NOT make policy decisions. For each due job it replays the
deterministic pipeline over the stored event, so classification and policy
are re-derived exactly as they were, and the executor's idempotency ledger
guarantees a job can never move money twice.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.payment_event import FailedTransactionEvent

logger = logging.getLogger(__name__)


@dataclass
class SchedulerRunReport:
    """What one worker pass did."""

    ran: int = 0
    recovered: int = 0
    failed: int = 0
    amount_recovered: int = 0
    job_ids: list[str] = field(default_factory=list)
    # Recovered amount and count per failure category, so a caller can
    # attribute deferred recoveries back to the scenario they came from.
    # Seconds each recovered job waited between being scheduled and running.
    # A deferred retry recovers real money, just later — that lag is the
    # cost of the cooldown and is worth reporting.
    delays_seconds: list[int] = field(default_factory=list)
    amount_by_category: dict[str, int] = field(default_factory=dict)
    count_by_category: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "recovered": self.recovered,
            "failed": self.failed,
            "amount_recovered": self.amount_recovered,
            "job_ids": list(self.job_ids),
            "delays_seconds": list(self.delays_seconds),
            "amount_by_category": dict(self.amount_by_category),
            "count_by_category": dict(self.count_by_category),
        }


def run_due_jobs(store, pipeline, now: datetime | None = None) -> SchedulerRunReport:
    """Execute every scheduled job whose cooldown has elapsed.

    Args:
        store: A ``RecoveryStateStore`` holding the scheduled jobs.
        pipeline: A ``RecoveryPipeline`` used to replay each event. It must
            NOT carry the same state store, or the job would simply be
            rescheduled instead of executed.
        now: Evaluation time; defaults to UTC now. Passing it lets the demo
            run a 24h cooldown without waiting.

    Returns:
        A ``SchedulerRunReport`` summarising the pass.
    """
    evaluated_at = now or datetime.now(timezone.utc)
    report = SchedulerRunReport()

    for job in store.due_jobs(evaluated_at):
        job_id = job["job_id"]
        try:
            event = FailedTransactionEvent(**json.loads(job["event_json"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scheduler: unreadable job %s: %s", job_id, exc)
            store.mark_job(job_id, "failed")
            report.ran += 1
            report.failed += 1
            report.job_ids.append(job_id)
            continue

        try:
            result = pipeline.process(event)
        except Exception as exc:  # noqa: BLE001
            logger.error("Scheduler: pipeline raised for %s: %s", job_id, exc)
            store.mark_job(job_id, "failed")
            report.ran += 1
            report.failed += 1
            report.job_ids.append(job_id)
            continue

        execution = result.execution
        recovered = 0
        if execution is not None and isinstance(execution.amount_recovered, int):
            recovered = execution.amount_recovered

        category = (
            result.classification.category.value
            if result.classification is not None
            else "unknown"
        )

        report.ran += 1
        report.job_ids.append(job_id)
        if recovered > 0:
            report.recovered += 1
            report.amount_recovered += recovered
            try:
                waited = int(
                    (
                        datetime.fromisoformat(job["next_eligible_at"])
                        - datetime.fromisoformat(job["created_at"])
                    ).total_seconds()
                )
                report.delays_seconds.append(max(0, waited))
            except Exception:  # noqa: BLE001
                # A malformed timestamp must not lose the recovery itself.
                report.delays_seconds.append(0)
            report.amount_by_category[category] = (
                report.amount_by_category.get(category, 0) + recovered
            )
            report.count_by_category[category] = (
                report.count_by_category.get(category, 0) + 1
            )
            store.mark_job(job_id, "done")
        else:
            report.failed += 1
            store.mark_job(job_id, "failed")

    return report
