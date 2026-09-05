/**
 * Headline KPI row for the Overview.
 *
 * Money first: revenue at risk and confirmed recovered are the numbers the
 * brief asks us to prove, so they lead.
 */

import type { BatchSummary } from "../types/dashboard";
import { formatPercent, formatRupeesCompact } from "../utils/format";

interface KpiTilesProps {
  summary: BatchSummary | null;
}

export default function KpiTiles({ summary }: KpiTilesProps) {
  const atRisk = summary?.total_attempted_amount ?? 0;
  const recovered = summary?.total_recovered_amount ?? 0;
  const rate = summary?.recovery_rate_by_amount ?? 0;
  // Recovered/recoverable measures the agent. Recovered/attempted is
  // dominated by cases policy correctly refused, and reads as failure.
  const ofRecoverable = summary?.recovery_rate_of_recoverable ?? 0;
  const recoverable = summary?.total_recoverable_amount ?? 0;
  const signals = summary?.funnel.needed_signal ?? 0;
  const raw = summary?.funnel.raw ?? 0;
  const selfResolved = Math.max(0, raw - signals);

  return (
    <div className="kpi-row" data-testid="kpi-tiles">
      <div className="stat-tile stat-tile--negative">
        <span className="stat-tile__label">Revenue at risk</span>
        <span className="stat-tile__value">{formatRupeesCompact(atRisk)}</span>
        <span className="stat-tile__sub">across {raw} failed payments</span>
      </div>

      <div className="stat-tile stat-tile--positive">
        <span className="stat-tile__label">Confirmed recovered</span>
        <span className="stat-tile__value">{formatRupeesCompact(recovered)}</span>
        <span className="stat-tile__sub">
          via a simulated capture, not an assumption
        </span>
      </div>

      <div className="stat-tile stat-tile--positive">
        <span className="stat-tile__label">Recovery rate</span>
        <span className="stat-tile__value">{formatPercent(ofRecoverable)}</span>
        <span className="stat-tile__sub">
          of what policy authorised chasing ({formatRupeesCompact(recoverable)});{" "}
          {formatPercent(rate)} of everything that failed
        </span>
      </div>

      <div className="stat-tile">
        <span className="stat-tile__label">Signals detected</span>
        <span className="stat-tile__value">
          {signals}
          <span className="stat-tile__value-of"> of {raw}</span>
        </span>
        <span className="stat-tile__sub">
          {selfResolved} resolved on their own
        </span>
      </div>
    </div>
  );
}
