/**
 * Outcome chips for a batch.
 *
 * Reads the pipeline's own outcome labels rather than re-deriving them, so
 * a chip can never disagree with the audit log.
 */

import type { BatchSummary } from "../types/dashboard";
import { humanize } from "../utils/format";

interface OutcomesBarProps {
  outcomes: BatchSummary["outcomes"] | null;
}

/** Outcome -> the signal colour already used by the status badges. */
const TONE: Record<string, string> = {
  recovered: "positive",
  pending: "warning",
  escalated: "warning",
  denied: "warning",
  execution_failed: "negative",
  audit_failed: "negative",
  recorded: "neutral",
};

export default function OutcomesBar({ outcomes }: OutcomesBarProps) {
  const entries = Object.entries(outcomes ?? {}).filter(([, n]) => n > 0);

  return (
    <section className="card outcomes" data-testid="outcomes-bar">
      <h2 className="card-title">Outcomes</h2>
      {entries.length === 0 ? (
        <p className="outcomes__empty">No outcomes yet.</p>
      ) : (
        <div className="outcomes__chips">
          {entries.map(([outcome, count]) => (
            <span
              key={outcome}
              className={`outcome-chip outcome-chip--${TONE[outcome] ?? "neutral"}`}
            >
              <span className="outcome-chip__count data-mono">{count}</span>
              {humanize(outcome)}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
