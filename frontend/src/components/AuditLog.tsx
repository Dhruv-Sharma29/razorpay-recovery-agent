/**
 * AuditLog component (TASK-013).
 *
 * Premium Fintech Redesign.
 * Table view of audit log entries. Read-only.
 * On mobile, transforms into cards via CSS.
 * No policy logic — only display.
 */

import { useEffect, useState } from "react";

import { getAuditLog } from "../api/client";
import type { AuditRecord } from "../types/dashboard";
import StatusBadge from "./StatusBadge";

export default function AuditLog() {
  const [records, setRecords] = useState<AuditRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function fetchLog() {
    setLoading(true);
    setError(null);
    try {
      const data = await getAuditLog();
      setRecords(data.records);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load audit log");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchLog();
  }, []);

  return (
    <div className="audit-log-container" data-testid="audit-log">
      <div className="card">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.5rem" }}>
          <h2 className="card-title" style={{ margin: 0 }}>Audit Trail</h2>
          <button className="btn btn--outline" onClick={fetchLog} disabled={loading} style={{ padding: "0.5rem 1.25rem", borderRadius: "99px" }}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        {error && (
          <div className="error-state" data-testid="audit-error">
            <div className="error-state__icon">⚠️</div>
            <div className="error-state__title">Failed to load audit log</div>
            <div className="error-state__description">{error}</div>
          </div>
        )}

        {!error && records.length === 0 && !loading && (
          <div className="empty-state" data-testid="audit-empty">
            <div className="empty-state__icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" style={{color: 'var(--text-muted)'}}>
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
                <polyline points="10 9 9 9 8 9"/>
              </svg>
            </div>
            <div className="empty-state__title">No audit records yet</div>
            <div className="empty-state__description">
              Process a payment to see entries appear here.
            </div>
          </div>
        )}

        {records.length > 0 && (
          <div className="audit-table-wrapper">
            <table className="audit-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Payment ID</th>
                  <th>Action</th>
                  <th>Outcome</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record) => {
                  const timeStr = new Date(record.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
                  const dateStr = new Date(record.timestamp).toLocaleDateString([], {month: 'short', day: 'numeric'});
                  return (
                    <tr key={record.audit_id}>
                      <td data-label="Time">
                        <div style={{display: 'flex', flexDirection: 'column'}}>
                          <span>{timeStr}</span>
                          <span style={{fontSize: '0.75rem', color: 'var(--text-muted)'}}>{dateStr}</span>
                        </div>
                      </td>
                      <td data-label="Payment ID" className="mono">{record.payment_id}</td>
                      <td data-label="Action" style={{ textTransform: "capitalize" }}>
                        {record.policy_action ? record.policy_action.replace(/_/g, " ") : "—"}
                      </td>
                      <td data-label="Outcome">
                        <StatusBadge outcome={record.final_outcome} />
                      </td>
                      <td data-label="Reason">
                        {record.classification_category ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
