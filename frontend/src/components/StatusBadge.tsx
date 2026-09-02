/**
 * StatusBadge component (TASK-013).
 *
 * Premium Fintech Redesign.
 * Displays a color-coded pill badge for pipeline outcome states.
 * SAFETY: Unknown/unsafe states are NEVER displayed as "recovered".
 * This component only renders — no policy logic.
 */

import type { FinalOutcome } from "../types/dashboard";

const LABELS: Record<FinalOutcome, string> = {
  recovered: "Recovered",
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
      {/* Icon rendering based on outcome */}
      {outcome === "recovered" && (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      )}
      {outcome === "denied" && (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="15" y1="9" x2="9" y2="15"/>
          <line x1="9" y1="9" x2="15" y2="15"/>
        </svg>
      )}
      {(outcome === "escalated" || outcome === "audit_failed") && (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
          <line x1="12" y1="9" x2="12" y2="13"/>
          <line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
      )}
      {(outcome === "execution_failed" || !LABELS[outcome]) && (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="15" y1="9" x2="9" y2="15"/>
          <line x1="9" y1="9" x2="15" y2="15"/>
        </svg>
      )}
      {outcome === "recorded" && (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
          <polyline points="10 9 9 9 8 9"/>
        </svg>
      )}
      {label}
    </span>
  );
}
