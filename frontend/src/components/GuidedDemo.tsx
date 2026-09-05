/**
 * Guided demo.
 *
 * Fires the golden-path record — an insufficient-funds failure priced just
 * under the auto-recovery cap — through the real pipeline via
 * POST /api/dashboard/golden-path, and logs what each stage actually
 * decided. Every line below is read from the response; none is scripted.
 *
 * On honesty about timing: the backend runs the pipeline as one atomic call,
 * so the stages become known together, when it returns. The log reveals them
 * in pipeline order rather than pretending to observe each one land, and the
 * header says the run has completed. Claiming live per-stage streaming would
 * require instrumenting the pipeline itself.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { runGoldenPath } from "../api/client";
import { formatRupees, humanize } from "../utils/format";
import type { DashboardResult } from "../types/dashboard";

interface GuidedDemoProps {
  open: boolean;
  onClose: () => void;
  /** Announces the recovered amount once the run lands. */
  onRecovered: (message: string) => void;
}

interface LogLine {
  key: string;
  label: string;
  value: string;
  /** Whether this stage permitted the recovery to continue. */
  tone: "ok" | "hold" | "note";
}

/** Pace of the reveal. Presentational only — the data is already in hand. */
const REVEAL_MS = 220;

function toLines(result: DashboardResult): LogLine[] {
  const allowed = result.automatic_recovery_allowed === true;
  return [
    {
      key: "classification",
      label: "Classification",
      value: result.failure_category
        ? humanize(result.failure_category)
        : "Not classified",
      tone: "note",
    },
    {
      key: "recommendation",
      label: "AI Recommendation",
      value: result.ai_suggested_action
        ? humanize(result.ai_suggested_action)
        : "No recommendation",
      tone: "note",
    },
    {
      key: "policy",
      label: "Policy Decision",
      value: result.policy_action
        ? `${humanize(result.policy_action)}${allowed ? "" : " — recovery refused"}`
        : "No decision",
      tone: allowed ? "ok" : "hold",
    },
    {
      key: "reasoning",
      label: "Reasoning",
      value: result.reasoning_recommendation ?? "No explanation produced",
      tone: "note",
    },
    {
      key: "execution",
      label: "Execution",
      value: result.execution_status
        ? humanize(result.execution_status)
        : "Not executed",
      tone: result.execution_status === "success" ? "ok" : "hold",
    },
    {
      key: "audit",
      label: "Audit",
      // /process reports the final outcome rather than the row id, so the log
      // states what was written instead of inventing a reference.
      value: result.final_outcome
        ? `Recorded · ${humanize(result.final_outcome)}`
        : "Not recorded",
      tone: result.final_outcome ? "ok" : "hold",
    },
  ];
}

export default function GuidedDemo({
  open,
  onClose,
  onRecovered,
}: GuidedDemoProps) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DashboardResult | null>(null);
  const timers = useRef<number[]>([]);
  const closeRef = useRef<HTMLButtonElement>(null);

  const clearTimers = useCallback(() => {
    timers.current.forEach((id) => window.clearTimeout(id));
    timers.current = [];
  }, []);

  const run = useCallback(async () => {
    clearTimers();
    setLines([]);
    setResult(null);
    setError(null);
    setRunning(true);
    try {
      const data = await runGoldenPath();
      setResult(data);
      const all = toLines(data);
      all.forEach((line, i) => {
        timers.current.push(
          window.setTimeout(() => setLines((prev) => [...prev, line]), i * REVEAL_MS),
        );
      });
      timers.current.push(
        window.setTimeout(() => {
          const recovered = data.amount_recovered ?? 0;
          onRecovered(
            recovered > 0
              ? `Recovered ${formatRupees(recovered)} — written to the audit log`
              : "Pipeline completed without recovering funds",
          );
        }, all.length * REVEAL_MS),
      );
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to run the guided demo",
      );
    } finally {
      setRunning(false);
    }
  }, [clearTimers, onRecovered]);

  // Escape closes, and focus moves into the dialog so keyboard users are not
  // stranded behind it.
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Pending reveals must not fire into an unmounted dialog.
  useEffect(() => {
    if (!open) clearTimers();
    return clearTimers;
  }, [open, clearTimers]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" data-testid="guided-demo-backdrop">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="guided-demo-title"
        data-testid="guided-demo"
      >
        <div className="modal__head">
          <h2 id="guided-demo-title" className="card-title">
            Guided demo
          </h2>
          <button
            type="button"
            ref={closeRef}
            className="modal__close"
            onClick={onClose}
            aria-label="Close guided demo"
            data-testid="guided-demo-close"
          >
            ×
          </button>
        </div>

        <p className="modal__lede">
          One insufficient-funds failure, priced just under the auto-recovery
          cap, through the real pipeline: classification, policy, reasoning,
          execution, audit. Every line below is read back from that run.
        </p>

        {error && (
          <div className="banner banner--error" role="alert">
            <strong>Couldn't run the demo.</strong> {error}
          </div>
        )}

        <button
          type="button"
          className="btn btn--primary"
          onClick={run}
          disabled={running}
          data-testid="guided-demo-run"
        >
          {running && <span className="btn__spinner" aria-hidden="true" />}
          {running ? "Running the pipeline…" : "Run guided demo"}
        </button>

        {running && (
          <p className="modal__lede" data-testid="guided-demo-waiting">
            Live reasoning calls the model twice — a recommendation and an
            explanation — so this can take up to half a minute.
          </p>
        )}

        {lines.length > 0 && (
          <ol className="demo-log" data-testid="guided-demo-log">
            {lines.map((line) => (
              <li
                key={line.key}
                className="demo-log__row"
                data-tone={line.tone}
                data-testid={`demo-log-${line.key}`}
              >
                <span className="demo-log__label">{line.label}</span>
                <span className="demo-log__value">{line.value}</span>
              </li>
            ))}
          </ol>
        )}

        {result && (
          <p className="demo-log__note" data-testid="guided-demo-summary">
            Pipeline complete. Recovered{" "}
            <strong>{formatRupees(result.amount_recovered ?? 0)}</strong> of{" "}
            {formatRupees(result.amount ?? 0)} attempted.
          </p>
        )}
      </div>
    </div>
  );
}
