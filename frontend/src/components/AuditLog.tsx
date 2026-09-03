/**
 * AuditLog component.
 *
 * Table view of audit log entries. Purely presentational — records,
 * loading state, and errors are owned by the parent (Dashboard) so
 * the log can auto-refresh right after a payment is processed.
 * No policy logic — only display.
 */

import type { AuditRecord } from "../types/dashboard";
import StatusBadge from "./StatusBadge";

interface AuditLogProps {
  records: AuditRecord[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

export default function AuditLog({
  records,
  loading,
  error,
  onRefresh,
}: AuditLogProps) {
  return (
    <div className="audit-log" data-testid="audit-log">
      <div className="card">
        <div className="audit-log__header">
          <h2 className="card-title">Audit Trail</h2>
          <button
            className="btn btn--ghost"
            onClick={onRefresh}
            disabled={loading}
          >
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
            <div className="empty-state__icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <rect x="4.5" y="3.5" width="15" height="17" rx="2.5" />
                <line x1="8" y1="8.5" x2="16" y2="8.5" strokeLinecap="round" />
                <line x1="8" y1="12" x2="16" y2="12" strokeLinecap="round" />
                <line x1="8" y1="15.5" x2="12.5" y2="15.5" strokeLinecap="round" />
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
                    <td className="mono">{record.event_id}</td>
                    <td className="mono">{record.payment_id}</td>
                    <td>{record.classification_category ?? "—"}</td>
                    <td>{record.policy_action ?? "—"}</td>
                    <td>
                      <StatusBadge outcome={record.final_outcome} />
                    </td>
                    <td className="data-mono">{record.timestamp}</td>
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
