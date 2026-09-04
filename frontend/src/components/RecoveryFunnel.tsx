/**
 * Funnel: raw events narrowed to money actually recovered.
 *
 * The stage definitions live in the backend harness so the funnel and the
 * KPIs can never drift apart. Each stage is a real filter over the one
 * above it, not a restatement of it.
 */

import type { FunnelCounts } from "../types/dashboard";

interface RecoveryFunnelProps {
  funnel: FunnelCounts | null;
}

const STAGES: { key: keyof FunnelCounts; label: string; definition: string }[] = [
  {
    key: "raw",
    label: "Raw event-cases",
    definition: "Every failed payment processed in this batch.",
  },
  {
    key: "needed_signal",
    label: "Needed a signal",
    definition:
      "Policy warranted an intervention — recovery allowed, or escalated. The rest resolved on their own.",
  },
  {
    key: "contacted",
    label: "Contacted",
    definition:
      "An action was actually attempted: executed inline, or a deferred retry the worker ran.",
  },
  {
    key: "confirmed_recovered",
    label: "Confirmed recovered",
    definition: "The payment was captured with a non-zero amount.",
  },
];

export default function RecoveryFunnel({ funnel }: RecoveryFunnelProps) {
  const raw = funnel?.raw ?? 0;

  return (
    <section className="card funnel" data-testid="recovery-funnel">
      <h2 className="card-title">From raw events to recovered revenue</h2>

      <div className="funnel__bars">
        {STAGES.map((stage) => {
          const value = funnel?.[stage.key] ?? 0;
          const width = raw > 0 ? Math.round((value / raw) * 100) : 0;
          return (
            <div className="funnel__row" key={stage.key}>
              <span className="funnel__label">{stage.label}</span>
              <div className="funnel__track">
                <div
                  className="funnel__fill"
                  style={{ width: `${width}%` }}
                  data-testid={`funnel-fill-${stage.key}`}
                />
              </div>
              <span className="funnel__value data-mono">{value}</span>
            </div>
          );
        })}
      </div>

      <p className="funnel__caption">
        Each stage is a real filter, not a restatement of the one above it.
      </p>

      <dl className="funnel__definitions">
        {STAGES.map((stage) => (
          <div key={stage.key}>
            <dt>{stage.label}</dt>
            <dd>{stage.definition}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
