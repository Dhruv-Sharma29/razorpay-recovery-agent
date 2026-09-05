/**
 * What the agent refused to do.
 *
 * Escalations read as failures on a dashboard. They are the opposite: each
 * one is a retry a naive "retry everything" agent would have fired at the
 * issuer. This panel restates the batch's own refusals as the cost avoided.
 *
 * Display only — every number comes from the batch response.
 */

import { formatRupees } from "../utils/format";
import type { RestraintSummary } from "../types/dashboard";

interface RestraintProps {
  restraint: RestraintSummary | null | undefined;
}

export default function Restraint({ restraint }: RestraintProps) {
  if (!restraint || restraint.extra_attempts === 0) return null;

  const rows: { label: string; value: string; testId: string }[] = [
    {
      label: "Attempts past the retry cap",
      value: String(restraint.attempts_past_retry_cap),
      testId: "restraint-past-cap",
    },
    {
      label: "Blind retries on an unknown cause",
      value: String(restraint.blind_retries_on_unknown_cause),
      testId: "restraint-unknown",
    },
    {
      label: "Non-retryable failures retried",
      value: String(restraint.non_retryable_retried),
      testId: "restraint-non-retryable",
    },
    {
      label: "Chased above the amount cap",
      value: formatRupees(restraint.amount_chased_past_cap),
      testId: "restraint-amount",
    },
  ];

  return (
    <section className="card" data-testid="restraint">
      <h2 className="card-title">What the agent refused to do</h2>
      <p className="restraint__lede">
        A retry-everything agent would have fired{" "}
        <strong data-testid="restraint-extra-attempts">
          {restraint.extra_attempts}
        </strong>{" "}
        more attempts at these payments. Each one is a real issuer hit and a
        chance to charge a customer twice.
      </p>
      <dl className="restraint__list">
        {rows.map((row) => (
          <div key={row.testId} className="restraint__row">
            <dt className="restraint__label">{row.label}</dt>
            <dd className="restraint__value data-mono" data-testid={row.testId}>
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
