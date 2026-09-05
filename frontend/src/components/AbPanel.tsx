/**
 * Did the AI earn its place?
 *
 * Runs the same batch twice, differing in exactly one variable: whether the
 * advisor may choose the action from the set policy permits. Anything else
 * would be measuring luck rather than the model.
 *
 * The result is reported whichever way it falls. A negative delta is a
 * finding, not a bug — it says the deterministic default is currently the
 * better policy, which is worth knowing.
 */

import { formatRupees } from "../utils/format";
import type { AbResult } from "../types/dashboard";

interface AbPanelProps {
  result: AbResult | null;
  running: boolean;
  error: string | null;
  onRun: () => void;
}

/** Seconds are unreadable at this scale; hours are how retries are discussed. */
function hours(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  return `${(seconds / 3600).toFixed(1)}h`;
}

function percent(rate: number | null): string {
  if (rate === null || rate === undefined) return "—";
  return `${Math.round(rate * 100)}%`;
}

/** Signed, so a regression reads as one at a glance. */
function signedPercent(rate: number | null): string {
  if (rate === null || rate === undefined) return "—";
  const points = Math.round(rate * 100);
  return `${points > 0 ? "+" : ""}${points}pp`;
}

function signedRupees(paise: number): string {
  return `${paise > 0 ? "+" : paise < 0 ? "−" : ""}${formatRupees(Math.abs(paise))}`;
}

function tone(value: number | null): string {
  if (!value) return "flat";
  return value > 0 ? "up" : "down";
}

export default function AbPanel({
  result,
  running,
  error,
  onRun,
}: AbPanelProps) {
  return (
    <section className="card" data-testid="ab-panel">
      <h2 className="card-title">Did the AI earn its place?</h2>
      <p className="ab__lede">
        The same batch, run twice. The only difference is whether the advisor
        may choose the action from the set policy permits.
      </p>

      <button
        type="button"
        className="btn btn--adversarial"
        onClick={onRun}
        disabled={running}
        data-testid="run-ab-btn"
      >
        {running && <span className="btn__spinner" aria-hidden="true" />}
        {running ? "Measuring…" : "Run the comparison"}
      </button>

      {error && (
        <div className="banner banner--error" role="alert">
          <strong>Couldn't run the comparison.</strong> {error}
        </div>
      )}

      {result && (
        <>
          <table className="ab__table" data-testid="ab-table">
            <thead>
              <tr>
                <th scope="col">Arm</th>
                <th scope="col">Recovered</th>
                <th scope="col">Rate</th>
                <th scope="col">Median</th>
              </tr>
            </thead>
            <tbody>
              {[result.control, result.treatment].map((arm, i) => (
                <tr key={arm.label} data-testid={i === 0 ? "ab-control" : "ab-treatment"}>
                  <th scope="row">{arm.label}</th>
                  <td className="data-mono">
                    {formatRupees(arm.recovered_amount)}
                  </td>
                  <td className="data-mono">
                    {percent(arm.recovery_rate_of_recoverable)}
                  </td>
                  <td className="data-mono">
                    {hours(arm.median_seconds_to_recovery)}
                  </td>
                </tr>
              ))}
              <tr className="ab__delta" data-testid="ab-delta">
                <th scope="row">Difference</th>
                <td
                  className="data-mono"
                  data-tone={tone(result.delta.recovered_amount)}
                >
                  {signedRupees(result.delta.recovered_amount)}
                </td>
                <td
                  className="data-mono"
                  data-tone={tone(result.delta.recovery_rate_of_recoverable)}
                >
                  {signedPercent(result.delta.recovery_rate_of_recoverable)}
                </td>
                <td className="data-mono">
                  {hours(result.delta.median_seconds_to_recovery)}
                </td>
              </tr>
            </tbody>
          </table>

          {/* An inconclusive run must never be presented as a result. */}
          {!result.conclusive ? (
            <p className="ab__note ab__note--warn" data-testid="ab-inconclusive">
              {result.note ??
                "The advisor made no action choices, so the arms are identical."}
            </p>
          ) : (
            <p className="ab__note" data-testid="ab-conclusive">
              The advisor changed{" "}
              <strong>{result.treatment.actions_chosen_by_model ?? 0}</strong>{" "}
              of {result.count_per_arm} actions. Reported whichever way it
              falls — a negative result says the deterministic default is
              currently the better policy.
            </p>
          )}
        </>
      )}
    </section>
  );
}
