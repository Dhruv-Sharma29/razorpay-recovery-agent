/**
 * Persistent top bar: AI status, recovery ticker, run controls, theme.
 *
 * The provider label is whatever the backend actually reports, so it can
 * never claim a model that is not in use.
 */

import BatchRunner from "../components/BatchRunner";
import ThemeToggle from "../components/ThemeToggle";
import { formatPercent, formatRupeesCompact } from "../utils/format";

interface TopBarProps {
  /** Configured provider, from the backend. Present even before any run. */
  providerModel: string | null;
  providerConfigured: boolean | null;
  /** Most recent *observed* result, if any event has been explained yet. */
  lastWasFallback: boolean | null;
  running: boolean;
  onRun: (count: number, runScheduler: boolean, explain: boolean) => void;
  onReset: () => void;
  lastRunSeconds?: number | null;
  lastRunCount?: number | null;
  /**
   * All-time recovered, from the audit log rather than this session, so the
   * figure survives a reload. Null until the first fetch resolves.
   */
  totalRecovered: number | null;
  /**
   * Share of what policy authorised chasing, from the most recent batch.
   * Deliberately not derived from the durable total: that denominator
   * includes every payment policy correctly refused, so it reads as failure.
   * Null until a batch has run, and then simply omitted.
   */
  recoveryRate: number | null;
}

/** Strip the vendor prefix so the pill stays readable. */
function shortProvider(model: string | null): string {
  if (!model) return "fallback";
  const tail = model.includes("/") ? model.split("/")[1] : model;
  return tail.split("-").slice(0, 2).join("-");
}

export default function TopBar({
  providerModel,
  providerConfigured,
  lastWasFallback,
  running,
  onRun,
  onReset,
  lastRunSeconds,
  lastRunCount,
  totalRecovered,
  recoveryRate,
}: TopBarProps) {
  // Report what we actually know, in order of confidence:
  //   1. an observed result  2. whether a key is configured  3. unknown.
  // Never claim "fallback" merely because nothing has run yet.
  const label = (() => {
    if (lastWasFallback === true) {
      return providerConfigured
        ? `${shortProvider(providerModel)} · fell back`
        : "deterministic fallback";
    }
    if (lastWasFallback === false) return shortProvider(providerModel);
    if (providerConfigured === true) return shortProvider(providerModel);
    if (providerConfigured === false) return "deterministic fallback";
    return "checking…";
  })();

  const live = lastWasFallback === false || (lastWasFallback === null && providerConfigured === true);

  return (
    <header className="topbar">
      <span
        className={`ai-pill${live ? " ai-pill--live" : ""}`}
        data-testid="ai-status-pill"
      >
        <span
          className={`ai-pill__dot${live ? " ai-pill__dot--live" : ""}`}
          aria-hidden="true"
        />
        {`AI agent on · ${label}`}
      </span>

      {/* Only rendered once there is a real figure to show — a placeholder
          zero would claim a measurement that has not been made. */}
      {totalRecovered !== null && (
        <div
          className="ticker"
          data-testid="recovery-ticker"
          role="status"
          aria-live="polite"
        >
          <span className="ticker__label">Recovered</span>
          <span className="ticker__value" data-testid="ticker-amount">
            {formatRupeesCompact(totalRecovered)}
          </span>
          {recoveryRate !== null && (
            <span className="ticker__rate" data-testid="ticker-rate">
              {formatPercent(recoveryRate)} of recoverable
            </span>
          )}
        </div>
      )}

      <div className="topbar__controls">
        <BatchRunner
          running={running}
          onRun={onRun}
          onReset={onReset}
          lastRunSeconds={lastRunSeconds}
          lastRunCount={lastRunCount}
        />
        <ThemeToggle />
      </div>
    </header>
  );
}
