/**
 * Persistent top bar: AI status, run controls, theme.
 *
 * The provider label is whatever the backend actually reports, so it can
 * never claim a model that is not in use.
 */

import BatchRunner from "../components/BatchRunner";
import ThemeToggle from "../components/ThemeToggle";

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
