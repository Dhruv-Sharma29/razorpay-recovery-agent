/**
 * Overview: the batch dashboard.
 *
 * Answers one question — how much money did this batch actually recover —
 * and shows the filtering that got there. Display only.
 */

import AiContribution from "../components/AiContribution";
import KpiTiles from "../components/KpiTiles";
import LiveFeed from "../components/LiveFeed";
import OutcomesBar from "../components/OutcomesBar";
import RecoveryActions from "../components/RecoveryActions";
import RecoveryByScenario from "../components/RecoveryByScenario";
import RecoveryFunnel from "../components/RecoveryFunnel";
import Restraint from "../components/Restraint";
import TimeToRecovery from "../components/TimeToRecovery";
import RevenueAtRisk from "../components/RevenueAtRisk";
import type {
  BatchCaseFrame,
  BatchSummary,
  RiskSummary,
} from "../types/dashboard";

interface OverviewProps {
  summary: BatchSummary | null;
  risk: RiskSummary | null;
  error: string | null;
  running: boolean;
  /** Cases streamed by the current or most recent run. */
  feed?: BatchCaseFrame[];
}

export default function Overview({
  summary,
  risk,
  error,
  running,
  feed = [],
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

      {summary?.recommendation && (
        <div className="banner banner--note" data-testid="overview-ai-summary">
          AI advisor: <span className="data-mono">{summary.recommendation.model}</span>
          {summary.recommendation.average_latency_ms
            ? ` · ${Math.round(summary.recommendation.average_latency_ms)}ms average latency`
            : " · latency unavailable"}
          {summary.recommendation.mode === "skipped"
            ? " · live recommendations skipped"
            : " · live recommendations enabled"}
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

      {/* Two content-driven columns: the left keeps its explanatory panels in
          flow, while the right can grow independently with its risk data. */}
      <div className="view__grid">
        <div className="view__col">
          <RecoveryFunnel
            funnel={summary?.funnel ?? null}
            outreach={summary?.outreach ?? null}
          />
          <RecoveryActions actions={summary?.recovery_actions ?? null} />
          <RecoveryByScenario scenarios={summary?.by_scenario ?? []} />
          <AiContribution summary={summary} />
        </div>
        <div className="view__stack">
          <LiveFeed cases={feed} running={running} />
          <OutcomesBar outcomes={summary?.outcomes ?? null} />
          <TimeToRecovery timing={summary?.timing} />
          <Restraint restraint={summary?.restraint} />
          <RevenueAtRisk risk={risk} />
        </div>
      </div>
    </div>
  );
}
