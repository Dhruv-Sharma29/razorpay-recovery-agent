/**
 * Recovery by failure category.
 *
 * Bar length is the amount at stake; the solid portion is what was
 * actually recovered. These are the pipeline's real categories — no
 * invented scenarios the engine does not actually handle.
 */

import type { ScenarioBreakdown } from "../types/dashboard";
import { formatRupees, humanize } from "../utils/format";

interface RecoveryByScenarioProps {
  scenarios: ScenarioBreakdown[];
}

export default function RecoveryByScenario({
  scenarios,
}: RecoveryByScenarioProps) {
  const maxAttempted = scenarios.reduce(
    (max, s) => Math.max(max, s.attempted_amount),
    0,
  );

  return (
    <section className="card scenarios" data-testid="recovery-by-scenario">
      <h2 className="card-title">Recovery by scenario</h2>

      {scenarios.length === 0 ? (
        <p className="scenarios__empty">Run a batch to see per-scenario recovery.</p>
      ) : (
        <div className="scenarios__list">
          {scenarios.map((s) => {
            const atStake =
              maxAttempted > 0 ? (s.attempted_amount / maxAttempted) * 100 : 0;
            const recovered =
              s.attempted_amount > 0
                ? (s.recovered_amount / s.attempted_amount) * 100
                : 0;
            return (
              <div className="scenarios__row" key={s.scenario}>
                <span className="scenarios__name">{humanize(s.scenario)}</span>
                <div className="scenarios__track">
                  <div
                    className="scenarios__at-stake"
                    style={{ width: `${atStake}%` }}
                  >
                    <div
                      className="scenarios__recovered"
                      style={{ width: `${recovered}%` }}
                      data-testid={`scenario-recovered-${s.scenario}`}
                    />
                  </div>
                </div>
                <span className="scenarios__amount data-mono">
                  {formatRupees(s.recovered_amount)} of{" "}
                  {formatRupees(s.attempted_amount)}
                  <span className="scenarios__pct">
                    {" "}
                    ({Math.round(s.recovery_rate_amount * 100)}%)
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      )}

      <div className="scenarios__legend">
        <span className="scenarios__key scenarios__key--stake" /> Amount at stake
        <span className="scenarios__key scenarios__key--recovered" /> Confirmed
        recovered
      </div>
      <p className="scenarios__note">
        These are the pipeline's real failure categories, not broader
        scenarios it does not handle.
      </p>
    </section>
  );
}
