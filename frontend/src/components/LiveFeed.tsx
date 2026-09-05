/**
 * Live case feed.
 *
 * A batch summary tells you what happened; this tells you it is happening.
 * Each row lands as the pipeline finishes with that payment, which is what
 * makes a run with live reasoning legible instead of a long silence.
 *
 * Display only — every row comes from the stream.
 */

import { formatRupees, humanize } from "../utils/format";
import type { BatchCaseFrame } from "../types/dashboard";

interface LiveFeedProps {
  cases: BatchCaseFrame[];
  running: boolean;
}

/** Newest first, and capped: a 200-case run should not build a huge list. */
const MAX_ROWS = 40;

function verdict(item: BatchCaseFrame): { label: string; tone: string } {
  if (item.recovered) return { label: "Recovered", tone: "ok" };
  if (!item.allowed) {
    return {
      label: item.escalation_reason
        ? humanize(item.escalation_reason)
        : "Escalated",
      tone: "held",
    };
  }
  return { label: item.action ? humanize(item.action) : "Actioned", tone: "run" };
}

export default function LiveFeed({ cases, running }: LiveFeedProps) {
  if (cases.length === 0) return null;

  const total = cases[0]?.total ?? cases.length;
  const rows = cases.slice(-MAX_ROWS).reverse();

  return (
    <section className="card" data-testid="live-feed">
      <h2 className="card-title">
        Live run
        <span className="live-feed__progress data-mono" data-testid="live-feed-progress">
          {cases.length} / {total}
        </span>
      </h2>
      <ol className="live-feed__list">
        {rows.map((item) => {
          const state = verdict(item);
          return (
            <li
              key={item.payment_id}
              className="live-feed__row"
              data-tone={state.tone}
            >
              <span className="live-feed__id data-mono">{item.payment_id}</span>
              <span className="live-feed__cause">
                {item.category ? humanize(item.category) : "Unclassified"}
              </span>
              <span className="live-feed__amount data-mono">
                {formatRupees(item.amount)}
              </span>
              <span className="live-feed__verdict">{state.label}</span>
            </li>
          );
        })}
      </ol>
      {running && (
        <p className="live-feed__status" role="status">
          Working through the batch…
        </p>
      )}
    </section>
  );
}
