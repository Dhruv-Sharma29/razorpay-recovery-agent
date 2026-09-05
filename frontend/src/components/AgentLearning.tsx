/**
 * What the agent has learned.
 *
 * These recovery rates are aggregated from the append-only audit log — they
 * are outcomes this system actually produced, not a configured table. The
 * same figures are fed to the advisor's prompt, which is what lets it pick
 * an action on evidence instead of guessing.
 *
 * On a fresh database there is nothing to show, and the panel says so rather
 * than inventing a prior.
 *
 * Display only.
 */

import { humanize } from "../utils/format";
import type { LearnedOutcomes } from "../types/dashboard";

interface AgentLearningProps {
  learned: LearnedOutcomes | null;
}

export default function AgentLearning({ learned }: AgentLearningProps) {
  if (!learned) return null;

  const rows = [...learned.rows].sort(
    (a, b) => b.success_rate - a.success_rate,
  );

  return (
    <section className="card" data-testid="agent-learning">
      <h2 className="card-title">What the agent has learned</h2>
      <p className="learning__lede">
        Recovery rates measured from its own audit log, not configured. These
        are fed to the advisor so it chooses on evidence.
      </p>

      {rows.length === 0 ? (
        <p className="learning__empty" data-testid="learning-empty">
          Nothing learned yet — run a batch, then run another. A rate is
          withheld until it rests on at least {learned.min_observations}{" "}
          attempts.
        </p>
      ) : (
        <ol className="learning__list">
          {rows.map((row) => (
            <li
              key={`${row.category}:${row.action}`}
              className="learning__row"
              data-testid={`learning-${row.category}-${row.action}`}
            >
              <span className="learning__cause">{humanize(row.category)}</span>
              <span className="learning__action">{humanize(row.action)}</span>
              <span className="learning__bar" aria-hidden="true">
                <span
                  className="learning__fill"
                  style={{ width: `${Math.round(row.success_rate * 100)}%` }}
                />
              </span>
              <span className="learning__rate data-mono">
                {Math.round(row.success_rate * 100)}%
              </span>
              <span className="learning__n data-mono">
                {row.recovered}/{row.attempts}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
