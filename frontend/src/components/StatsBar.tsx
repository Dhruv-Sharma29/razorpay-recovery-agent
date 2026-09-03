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

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { AuditRecord } from "../types/dashboard";

interface StatsBarProps {
  records: AuditRecord[];
}

function formatInr(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
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
      {/* Recovery rate stays the headline metric, but is sized like every
          other tile — the dark fill carries the emphasis, not the box. */}
      <div className="stat-tile stat-tile--hero">
        <span className="stat-tile__label">Recovery Rate</span>
        <span className="stat-tile__value data-mono">
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
        <span className="stat-tile__value data-mono">
          {formatInr(stats.amountRecovered)}
        </span>
      </div>

      {categoryStats.length > 0 && (
        <div className="category-chart" data-testid="category-chart">
          <span className="stat-tile__label">Recovery Rate by Category</span>
          {/* Height scales with category count and interval={0} forces
              every tick, so recharts can't silently drop labels it
              predicts would overlap. */}
          <ResponsiveContainer
            width="100%"
            height={Math.max(160, categoryStats.length * 42)}
          >
            <BarChart
              data={categoryStats}
              layout="vertical"
              margin={{ top: 8, right: 16, bottom: 0, left: 0 }}
            >
              <XAxis
                type="number"
                domain={[0, 100]}
                unit="%"
                tick={{ fontSize: 11, fill: "var(--text-secondary)" }}
                stroke="var(--border-strong)"
              />
              <YAxis
                type="category"
                dataKey="category"
                width={160}
                interval={0}
                tick={{ fontSize: 12, fill: "var(--text-secondary)" }}
                stroke="var(--border-strong)"
              />
              <Tooltip
                formatter={(v) => `${v}%`}
                cursor={{ fill: "rgba(36, 82, 232, 0.06)" }}
                contentStyle={{
                  background: "var(--bg-card)",
                  border: "1px solid var(--border-default)",
                  borderRadius: "8px",
                  color: "var(--text-primary)",
                }}
              />
              <Bar
                dataKey="recoveryRate"
                fill="var(--brand-primary)"
                radius={[0, 4, 4, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
