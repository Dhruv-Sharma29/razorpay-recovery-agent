/**
 * Batch runner control.
 *
 * Each run generates fresh event ids server-side, so clicking repeatedly
 * accumulates results rather than colliding on idempotency. Reset clears
 * recovery state only — audit history is append-only and is preserved.
 */

import { useState } from "react";

const MIN_COUNT = 1;
const MAX_COUNT = 500;

interface BatchRunnerProps {
  running: boolean;
  onRun: (count: number, runScheduler: boolean) => void;
  onReset: () => void;
  lastRunSeconds?: number | null;
  lastRunCount?: number | null;
}

function clamp(value: number): number {
  if (!Number.isFinite(value)) return MIN_COUNT;
  return Math.min(Math.max(Math.round(value), MIN_COUNT), MAX_COUNT);
}

/**
 * The count is held as text while editing.
 *
 * Clamping on every keystroke made the field unusable: clearing it yielded
 * NaN, which snapped back to the minimum of 1, so the next digit typed
 * appended to it (5 became 15). The value is only normalised on blur and
 * at submit, which is where the bound actually matters.
 */
function parseCount(text: string): number {
  return clamp(parseInt(text, 10));
}

export default function BatchRunner({
  running,
  onRun,
  onReset,
  lastRunSeconds,
  lastRunCount,
}: BatchRunnerProps) {
  const [countText, setCountText] = useState("25");
  const [runScheduler, setRunScheduler] = useState(true);
  const [optionsOpen, setOptionsOpen] = useState(false);

  return (
    <div className="batch-runner">
      {lastRunSeconds != null && lastRunCount != null && !running && (
        <span className="batch-runner__toast" role="status">
          Ran {lastRunCount} in {lastRunSeconds.toFixed(1)}s
        </span>
      )}

      <div className="batch-runner__options">
        <button
          type="button"
          className="btn btn--ghost"
          aria-expanded={optionsOpen}
          onClick={() => setOptionsOpen((open) => !open)}
          data-testid="run-options-toggle"
        >
          Run options
        </button>
        {optionsOpen && (
          <div className="batch-runner__menu" role="group" aria-label="Run options">
            <label className="batch-runner__check">
              <input
                type="checkbox"
                checked={runScheduler}
                onChange={(e) => setRunScheduler(e.target.checked)}
                data-testid="run-scheduler-toggle"
              />
              Run deferred retries to completion
            </label>
            <p className="batch-runner__note">
              Off leaves scheduled retries pending, so their money is not
              counted yet.
            </p>
            <button
              type="button"
              className="btn btn--adversarial"
              onClick={() => {
                setOptionsOpen(false);
                onReset();
              }}
              disabled={running}
              data-testid="reset-btn"
            >
              Reset recovery state
            </button>
            <p className="batch-runner__note">
              Audit history is append-only and is never cleared.
            </p>
          </div>
        )}
      </div>

      <label className="batch-runner__count">
        <span className="sr-only">Transactions to process</span>
        <input
          type="number"
          min={MIN_COUNT}
          max={MAX_COUNT}
          value={countText}
          disabled={running}
          // Store the raw text so the field can be emptied while editing.
          onChange={(e) => setCountText(e.target.value)}
          // Normalise once the user is done, not mid-keystroke.
          onBlur={() => setCountText(String(parseCount(countText)))}
          aria-label="Transactions to process"
          data-testid="batch-count"
        />
      </label>

      <button
        type="button"
        className="btn btn--primary"
        onClick={() => onRun(parseCount(countText), runScheduler)}
        disabled={running}
        data-testid="run-batch-btn"
      >
        {running && <span className="btn__spinner" aria-hidden="true" />}
        {running ? "Running…" : "Run batch"}
      </button>
    </div>
  );
}
