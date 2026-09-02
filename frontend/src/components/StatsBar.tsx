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

  return (
    <div className="stats-bar" data-testid="stats-bar">
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
        <span className="stat-tile__value">{formatInr(stats.amountRecovered)}</span>
      </div>
      <div className="stat-tile">
        <span className="stat-tile__label">Recovery Rate</span>
        <span className="stat-tile__value">{stats.recoveryRate.toFixed(1)}%</span>
      </div>
    </div>
  );
}
