/**
 * How long recovery actually took.
 *
 * Recovering the money matters; recovering it a day late is a different
 * product. A retry deferred behind a cooldown carries that wait as real
 * elapsed time, so this is what separates "recovered" from "recovered
 * quickly" — and it is the metric that would expose a policy tuned to look
 * good on rate alone.
 *
 * Display only — every number comes from the batch response.
 */

import type { BatchTiming } from "../types/dashboard";

interface TimeToRecoveryProps {
  timing: BatchTiming | null | undefined;
}

/** Seconds are unreadable at retry scale; cooldowns are discussed in hours. */
function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const hrs = seconds / 3600;
  return hrs < 48 ? `${hrs.toFixed(1)}h` : `${(hrs / 24).toFixed(1)}d`;
}

export default function TimeToRecovery({ timing }: TimeToRecoveryProps) {
  if (!timing || timing.recovered_count === 0) return null;

  const deferred = Math.max(0, timing.recovered_count - timing.instant_count);

  return (
    <section className="card" data-testid="time-to-recovery">
      <h2 className="card-title">Time to recovery</h2>
      <div className="ttr__grid">
        <div className="ttr__metric">
          <span className="ttr__label">Median</span>
          <span className="ttr__value data-mono" data-testid="ttr-median">
            {duration(timing.median_seconds)}
          </span>
        </div>
        <div className="ttr__metric">
          <span className="ttr__label">Slowest</span>
          <span className="ttr__value data-mono" data-testid="ttr-max">
            {duration(timing.max_seconds)}
          </span>
        </div>
        <div className="ttr__metric">
          <span className="ttr__label">Immediate</span>
          <span className="ttr__value data-mono" data-testid="ttr-instant">
            {timing.instant_count}
          </span>
        </div>
        <div className="ttr__metric">
          <span className="ttr__label">After a cooldown</span>
          <span className="ttr__value data-mono" data-testid="ttr-deferred">
            {deferred}
          </span>
        </div>
      </div>
      <p className="ttr__note">
        Measured across {timing.recovered_count} recovered payments. A deferred
        retry carries its full cooldown, so waiting shows up as cost here even
        when it improves the recovery rate.
      </p>
    </section>
  );
}
