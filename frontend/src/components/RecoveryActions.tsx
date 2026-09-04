/**
 * Recovery Actions breakdown.
 *
 * Executor-level outcome counts for a batch run. Distinct from the
 * RecoveryFunnel (which shows signal-filtering stages) and from
 * AiContribution (which shows reasoning stats).
 *
 * Display only — every number comes from the batch response.
 */

import type { RecoveryActions as RecoveryActionsType } from "../types/dashboard";

interface RecoveryActionsProps {
  actions: RecoveryActionsType | null | undefined;
}

export default function RecoveryActions({ actions }: RecoveryActionsProps) {
  if (!actions) return null;

  const tiles: { label: string; value: number; testId: string }[] = [
    {
      label: "Retries attempted",
      value: actions.retries_attempted,
      testId: "ra-retries-attempted",
    },
    {
      label: "Payments recovered",
      value: actions.payments_recovered,
      testId: "ra-payments-recovered",
    },
    {
      label: "Payments pending",
      value: actions.payments_pending,
      testId: "ra-payments-pending",
    },
    {
      label: "Payments escalated",
      value: actions.payments_escalated,
      testId: "ra-payments-escalated",
    },
    {
      label: "Execution failed",
      value: actions.execution_failed,
      testId: "ra-execution-failed",
    },
  ];

  return (
    <section className="card" data-testid="recovery-actions">
      <h2 className="card-title">Recovery Actions</h2>
      <div className="ai-card__grid">
        {tiles.map((tile) => (
          <div key={tile.testId} className="ai-metric">
            <span className="ai-metric__label">{tile.label}</span>
            <span
              className="ai-metric__value data-mono"
              data-testid={tile.testId}
            >
              {tile.value}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
