/**
 * Overview: the batch dashboard.
 *
 * Answers one question — how much money did this batch actually recover —
 * and shows the filtering that got there. Display only.
 */

import AiContribution from "../components/AiContribution";
import KpiTiles from "../components/KpiTiles";
import OutcomesBar from "../components/OutcomesBar";
import RecoveryByScenario from "../components/RecoveryByScenario";
import RecoveryFunnel from "../components/RecoveryFunnel";
import RevenueAtRisk from "../components/RevenueAtRisk";
import type { BatchSummary, RiskSummary } from "../types/dashboard";

interface OverviewProps {
  summary: BatchSummary | null;
  risk: RiskSummary | null;
  error: string | null;
  running: boolean;
}

export default function Overview({
  summary,
  risk,
  error,
  running,
}: OverviewProps) {
  return (
    <div className="view" data-testid="view-overview">
      <header className="view__header">
        <h1>Overview</h1>
        <p>
          {summary
            ? `Most recent batch · ${summary.transactions_processed} signals processed`
            : "Run a batch to measure how much revenue the agent recovers."}
        </p>
      </header>

      {error && (
        <div className="banner banner--error" role="alert">
          <strong>Couldn't run the batch.</strong> {error}
        </div>
      )}

      {summary?.simulated && (
        <div className="banner banner--note">
          Test mode: payments are simulated, so recovered amounts come from a
          sandbox capture rather than a live gateway.
        </div>
      )}

      <KpiTiles summary={summary} />

      {!summary && !running && !error && (
        <div className="empty-state" data-testid="overview-empty">
          <div className="empty-state__title">No batch run yet</div>
          <div className="empty-state__description">
            Use “Run batch” above to process a fresh set of failed payments
            through the recovery pipeline.
          </div>
        </div>
      )}

      {/* Two columns: the left stacks the funnel above the scenario
          breakdown, the right holds the supporting panels. Both columns
          stretch to the same height, so the right panel's top and bottom
          line up with the first and last card on the left. */}
      <div className="view__grid">
        <div className="view__col">
          <RecoveryFunnel funnel={summary?.funnel ?? null} />
          <RecoveryByScenario scenarios={summary?.by_scenario ?? []} />
        </div>
        <div className="view__stack">
          <OutcomesBar outcomes={summary?.outcomes ?? null} />
          <RevenueAtRisk risk={risk} />
        </div>
      </div>

      {/* Secondary, explanatory: what the model contributed to the run
          above. Placed after the recovery KPIs so it never competes with
          them for attention. */}
      <AiContribution summary={summary} />
    </div>
  );
}
