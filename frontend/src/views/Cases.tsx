/**
 * Cases: every audit record with its full decision chain.
 *
 * cause -> rule -> bound -> action -> outcome -> recovered amount, so a
 * reviewer can audit one row without replaying the pipeline.
 */

import { Fragment, useEffect, useMemo, useState } from "react";

import StatusBadge from "../components/StatusBadge";
import PendingRetryCountdown from "../components/PendingRetryCountdown";
import type { AuditRecord, FinalOutcome } from "../types/dashboard";
import { formatRupees, humanize } from "../utils/format";

const PAGE_SIZE = 15;
const OUTCOME_OPTIONS: FinalOutcome[] = [
  "recovered",
  "pending",
  "escalated",
  "denied",
  "execution_failed",
  "recorded",
  "audit_failed",
];

interface CasesProps {
  records: AuditRecord[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

export default function Cases({ records, loading, error, onRefresh }: CasesProps) {
  const [outcome, setOutcome] = useState<string>("all");
  const [category, setCategory] = useState<string>("all");
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const categories = useMemo(
    () =>
      Array.from(
        new Set(records.map((r) => r.classification_category ?? "unknown")),
      ).sort(),
    [records],
  );

  const filtered = useMemo(
    () =>
      records.filter(
        (r) =>
          (outcome === "all" || r.final_outcome === outcome) &&
          (category === "all" ||
            (r.classification_category ?? "unknown") === category),
      ),
    [records, outcome, category],
  );

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const visible = filtered.slice(
    safePage * PAGE_SIZE,
    safePage * PAGE_SIZE + PAGE_SIZE,
  );

  return (
    <div className="view" data-testid="view-cases">
      <header className="view__header">
        <h1>Cases</h1>
        <p>{filtered.length} records · full decision chain per row</p>
      </header>

      <div className="cases__controls">
        <label>
          <span>Outcome</span>
          <select
            value={outcome}
            onChange={(e) => {
              setOutcome(e.target.value);
              setPage(0);
            }}
            data-testid="filter-outcome"
          >
            <option value="all">All outcomes</option>
            {OUTCOME_OPTIONS.map(
              (o) => (
                <option key={o} value={o}>
                  {humanize(o)}
                </option>
              ),
            )}
          </select>
        </label>

        <label>
          <span>Category</span>
          <select
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(0);
            }}
            data-testid="filter-category"
          >
            <option value="all">All categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {humanize(c)}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          className="btn btn--ghost"
          onClick={onRefresh}
          disabled={loading}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="banner banner--error" role="alert">
          <strong>Couldn't load cases.</strong> {error}
        </div>
      )}

      {filtered.length === 0 && !loading ? (
        <div className="empty-state" data-testid="cases-empty">
          <div className="empty-state__title">No matching cases</div>
          <div className="empty-state__description">
            Run a batch, or widen the filters above.
          </div>
        </div>
      ) : (
        <div className="audit-table-wrapper">
          <table className="audit-table">
            <thead>
              <tr>
                <th>Payment</th>
                <th>Cause</th>
                <th>Action</th>
                <th>Outcome</th>
                <th>Recovered</th>
                <th aria-label="Expand" />
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => {
                const open = expanded === r.audit_id;
                return (
                  <Fragment key={r.audit_id}>
                    <tr>
                      <td className="mono">{r.payment_id}</td>
                      <td>{humanize(r.classification_category ?? "unknown")}</td>
                      <td>{r.policy_action ?? "—"}</td>
                      <td>
                        <StatusBadge outcome={r.final_outcome as FinalOutcome} />
                        {r.final_outcome === "pending" && r.scheduled_for && (
                          <PendingRetryCountdown
                            scheduledFor={r.scheduled_for}
                            auditId={r.audit_id}
                          />
                        )}
                      </td>
                      <td className="data-mono">
                        {formatRupees(r.amount_recovered ?? 0)}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn--ghost btn--tiny"
                          aria-expanded={open}
                          onClick={() => setExpanded(open ? null : r.audit_id)}
                          data-testid={`expand-${r.audit_id}`}
                        >
                          {open ? "Hide" : "Chain"}
                        </button>
                      </td>
                    </tr>
                    {open && (
                      <tr className="cases__chain-row">
                        <td colSpan={6}>
                          <ChainDetail record={r} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {pageCount > 1 && (
        <div className="cases__pager">
          <button
            type="button"
            className="btn btn--ghost"
            disabled={safePage === 0}
            onClick={() => setPage(safePage - 1)}
          >
            Previous
          </button>
          <span className="data-mono">
            Page {safePage + 1} of {pageCount}
          </span>
          <button
            type="button"
            className="btn btn--ghost"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage(safePage + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function ChainDetail({ record }: { record: AuditRecord }) {
  const steps: { label: string; value: string }[] = [
    {
      label: "Cause",
      value: `${humanize(record.classification_category ?? "unknown")} · ${
        record.classification_rule_id ?? "no rule"
      }`,
    },
    {
      label: "AI recommendation",
      value: [
        record.recommendation_suggested_cause
          ? humanize(record.recommendation_suggested_cause)
          : null,
        record.recommendation_suggested_action
          ? humanize(record.recommendation_suggested_action)
          : null,
        record.recommendation_status
          ? humanize(record.recommendation_status)
          : "unavailable",
      ]
        .filter(Boolean)
        .join(" · "),
    },
    {
      label: "AI telemetry",
      value: [
        record.recommendation_revenue_at_risk === true
          ? "revenue at risk"
          : record.recommendation_revenue_at_risk === false
            ? "not marked at risk"
            : null,
        record.recommendation_risk_score != null
          ? `risk ${Math.round(record.recommendation_risk_score * 100)}%`
          : null,
        record.recommendation_confidence != null
          ? `confidence ${Math.round(record.recommendation_confidence * 100)}%`
          : null,
        record.recommendation_latency_ms != null
          ? `${record.recommendation_latency_ms}ms`
          : null,
      ]
        .filter(Boolean)
        .join(" · ") || "—",
    },
    {
      label: "AI model",
      value: record.recommendation_model
        ? `${record.recommendation_model}${
            record.recommendation_prompt_version
              ? ` · prompt ${record.recommendation_prompt_version}`
              : ""
          }`
        : "—",
    },
    {
      label: "Rule",
      value: record.policy_rule_id ?? "—",
    },
    {
      label: "Bound",
      value: [
        record.amount_limit != null
          ? `cap ${formatRupees(record.amount_limit)}`
          : null,
        record.max_retries != null ? `max ${record.max_retries} retries` : null,
        record.cooldown_seconds
          ? `${Math.round(record.cooldown_seconds / 3600)}h cooldown`
          : null,
      ]
        .filter(Boolean)
        .join(" · ") || "—",
    },
    { label: "Action", value: record.policy_action ?? "—" },
    {
      label: "Outcome",
      value: `${record.final_outcome}${
        record.payment_status ? ` · ${record.payment_status}` : ""
      }`,
    },
    {
      label: "Recovered",
      value: formatRupees(record.amount_recovered ?? 0),
    },
  ];

  return (
    <div className="chain" data-testid={`chain-${record.audit_id}`}>
      {steps.map((step) => (
        <div className="chain__step" key={step.label}>
          <span className="chain__label">{step.label}</span>
          <span className="chain__value">{step.value}</span>
        </div>
      ))}
      {record.scheduled_for && (
        <div className="chain__step">
          <span className="chain__label">Scheduled for</span>
          <span className="chain__value data-mono">{record.scheduled_for}</span>
        </div>
      )}
      {record.final_outcome === "pending" && record.scheduled_for && (
        <div className="chain__step">
          <span className="chain__label">Retry countdown</span>
          <PendingRetryCountdown
            scheduledFor={record.scheduled_for}
            auditId={record.audit_id}
            inline
          />
        </div>
      )}
      {record.escalation_trigger && (
        <div className="chain__step">
          <span className="chain__label">Escalation</span>
          <span className="chain__value">{record.escalation_trigger}</span>
        </div>
      )}
    </div>
  );
}
