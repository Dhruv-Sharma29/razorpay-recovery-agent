/**
 * AuditLog component (TASK-009).
 *
 * Table view of audit log entries. Read-only.
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
    <div className="audit-log" data-testid="audit-log">
      <div className="card">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
          <h2 className="card-title" style={{ margin: 0 }}>Audit Trail</h2>
          <button className="btn btn--primary" onClick={fetchLog} disabled={loading} style={{ padding: "0.4rem 1rem", fontSize: "0.78rem" }}>
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>

        {error && (
          <div className="error-state" data-testid="audit-error">
            <div className="error-state__title">Failed to load audit log</div>
            <div className="error-state__description">{error}</div>
          </div>
        )}

        {!error && records.length === 0 && !loading && (
          <div className="empty-state" data-testid="audit-empty">
            <div className="empty-state__icon">📋</div>
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
                  <th>Event ID</th>
                  <th>Payment ID</th>
                  <th>Category</th>
                  <th>Policy Action</th>
                  <th>Outcome</th>
                  <th>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record) => (
                  <tr key={record.audit_id}>
                    <td style={{ fontFamily: "monospace" }}>{record.event_id}</td>
                    <td style={{ fontFamily: "monospace" }}>{record.payment_id}</td>
                    <td>{record.classification_category ?? "—"}</td>
                    <td>{record.policy_action ?? "—"}</td>
                    <td>
                      <StatusBadge outcome={record.final_outcome} />
                    </td>
                    <td>{record.timestamp}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
