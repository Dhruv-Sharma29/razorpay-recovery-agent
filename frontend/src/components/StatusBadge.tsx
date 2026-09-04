/**
 * StatusBadge component (TASK-009).
 *
 * Displays a color-coded badge for pipeline outcome states.
 * SAFETY: Unknown/unsafe states are NEVER displayed as "recovered".
 * This component only renders — no policy logic.
 */

import type { FinalOutcome } from "../types/dashboard";

const LABELS: Record<FinalOutcome, string> = {
  recovered: "Recovered",
  pending: "Pending",
  denied: "Denied",
  escalated: "Escalated",
  execution_failed: "Failed",
  recorded: "Recorded",
  audit_failed: "Audit Failed",
};

interface StatusBadgeProps {
  outcome: FinalOutcome;
}

export default function StatusBadge({ outcome }: StatusBadgeProps) {
  const label = LABELS[outcome] ?? "Unknown";
  const className = `status-badge status-badge--${outcome}`;

  return (
    <span className={className} data-testid="status-badge">
      {label}
    </span>
  );
}
