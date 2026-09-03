/**
 * StatsBar component.
 *
 * Read-only summary tiles computed from audit log records already
 * returned by the backend (Total processed, Recovered, Escalated,
 * Exceptions, Amount recovered, Recovery rate).
 *
 * This is pure display arithmetic over backend-provided outcomes —
 * it does not classify, decide, or reinterpret any policy outcome.
 */

import { lazy, Suspense, useMemo } from "react";

import type { AuditRecord } from "../types/dashboard";
import { formatInr } from "../utils/currency";

/** Deferred so recharts is only downloaded when a chart is actually shown. */
const CategoryChart = lazy(() => import("./CategoryChart"));

interface StatsBarProps {
  records: AuditRecord[];
}

export default function StatsBar({ records }: StatsBarProps) {
  const stats = useMemo(() => {
    const total = records.length;
    const recovered = records.filter((r) => r.final_outcome === "recovered");
    const escalated = records.filter((r) => r.final_outcome === "escalated").length;
    const exceptions = records.filter(
      (r) =>
        r.final_outcome === "execution_failed" || r.final_outcome === "audit_failed",
    ).length;
    const amountRecovered = recovered.reduce((sum, r) => sum + (r.amount ?? 0), 0);
    const recoveryRate = total > 0 ? (recovered.length / total) * 100 : 0;

    return {
      total,
      recoveredCount: recovered.length,
      escalated,
      exceptions,
      amountRecovered,
      recoveryRate,
    };
  }, [records]);

  const categoryStats = useMemo(() => {
    const byCategory = new Map<string, { total: number; recovered: number }>();
    for (const r of records) {
      const cat = r.classification_category ?? "unknown";
      const entry = byCategory.get(cat) ?? { total: 0, recovered: 0 };
      entry.total += 1;
      if (r.final_outcome === "recovered") entry.recovered += 1;
      byCategory.set(cat, entry);
    }
    return Array.from(byCategory.entries()).map(
      ([category, { total, recovered }]) => ({
        category,
        recoveryRate: total > 0 ? Math.round((recovered / total) * 100) : 0,
      }),
    );
  }, [records]);

  return (
    <div className="stats-bar" data-testid="stats-bar">
      {/* Leads the row as the headline metric. Styled identically to its
          siblings — green marks it as a positive-outcome measure, the
          same signal "Recovered" carries. */}
      <div className="stat-tile stat-tile--positive">
        <span className="stat-tile__label">Recovery Rate</span>
        <span className="stat-tile__value">
          {stats.recoveryRate.toFixed(1)}%
        </span>
      </div>
      <div className="stat-tile">
        <span className="stat-tile__label">Total Processed</span>
        <span className="stat-tile__value">{stats.total}</span>
      </div>
      <div className="stat-tile stat-tile--positive">
        <span className="stat-tile__label">Recovered</span>
        <span className="stat-tile__value">{stats.recoveredCount}</span>
      </div>
      <div className="stat-tile stat-tile--warning">
        <span className="stat-tile__label">Escalated</span>
        <span className="stat-tile__value">{stats.escalated}</span>
      </div>
      <div className="stat-tile stat-tile--negative">
        <span className="stat-tile__label">Exceptions</span>
        <span className="stat-tile__value">{stats.exceptions}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-tile__label">Amount Recovered</span>
        <span className="stat-tile__value">
          {formatInr(stats.amountRecovered)}
        </span>
      </div>

      {categoryStats.length > 0 && (
        <div className="category-chart" data-testid="category-chart">
          <span className="stat-tile__label">Recovery Rate by Category</span>
          <Suspense
            fallback={
              // Same height the chart will occupy, so nothing jumps when
              // the deferred chunk lands.
              <div
                className="category-chart__loading"
                style={{ height: Math.max(160, categoryStats.length * 42) }}
                aria-hidden="true"
              />
            }
          >
            <CategoryChart data={categoryStats} />
          </Suspense>
        </div>
      )}
    </div>
  );
}
