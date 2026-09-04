/**
 * Revenue at risk.
 *
 * Aggregated from the audit log, so it reflects everything the pipeline
 * has ever seen rather than just the latest batch. Customers are grouped
 * by a pseudonymous reference — the raw id is never stored.
 */

import type { RiskSummary } from "../types/dashboard";
import { formatRupees, formatRupeesCompact } from "../utils/format";

interface RevenueAtRiskProps {
  risk: RiskSummary | null;
}

export default function RevenueAtRisk({ risk }: RevenueAtRiskProps) {
  if (!risk || risk.records_considered === 0) {
    return (
      <section className="card risk" data-testid="revenue-at-risk">
        <h2 className="card-title">Revenue at risk</h2>
        <p className="risk__empty">
          Run a batch to build up an at-risk picture across merchants and
          subscriptions.
        </p>
      </section>
    );
  }

  const sub = risk.subscription_failures;

  return (
    <section className="card risk" data-testid="revenue-at-risk">
      <h2 className="card-title">Revenue at risk</h2>

      <p className="risk__headline">
        <span className="risk__outstanding data-mono">
          {formatRupeesCompact(risk.outstanding_amount)}
        </span>{" "}
        still outstanding across {risk.records_considered} recorded failures.
      </p>

      <div className="risk__block">
        <h3 className="risk__subhead">Most exposed merchants</h3>
        {risk.by_merchant.length === 0 ? (
          <p className="risk__empty">No merchant activity yet.</p>
        ) : (
          <ul className="risk__list">
            {risk.by_merchant.map((m) => (
              <li key={m.merchant_id}>
                <span className="risk__key">{m.merchant_id}</span>
                <span className="risk__value data-mono">
                  {formatRupees(m.outstanding_amount)}
                </span>
                <span className="risk__meta">{m.failures} failures</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="risk__block">
        <h3 className="risk__subhead">Repeat failures</h3>
        {risk.repeat_customers.length === 0 ? (
          <p className="risk__empty">
            No customer has failed more than once yet.
          </p>
        ) : (
          <ul className="risk__list">
            {risk.repeat_customers.map((c) => (
              <li key={c.customer_ref}>
                <span className="risk__key data-mono">{c.customer_ref}</span>
                <span className="risk__value data-mono">
                  {formatRupees(c.at_risk_amount)}
                </span>
                <span className="risk__meta">{c.failures} failures</span>
              </li>
            ))}
          </ul>
        )}
        <p className="risk__note">
          Grouped by a pseudonymous reference; customer identifiers are never
          stored.
        </p>
      </div>

      <div className="risk__block">
        <h3 className="risk__subhead">Subscription renewals</h3>
        <p className="risk__meta">
          {sub.count} failed renewals worth{" "}
          <span className="data-mono">{formatRupees(sub.at_risk_amount)}</span>.{" "}
          {sub.mandate_issues > 0
            ? `${sub.mandate_issues} need re-authorization — an expired or paused mandate cannot be retried into success.`
            : "No mandate problems detected."}
        </p>
      </div>
    </section>
  );
}
