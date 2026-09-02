/**
 * Dashboard component.
 *
 * Main PayPulse console: a demo payment form, pipeline result display,
 * summary stats, and a live audit trail.
 *
 * SAFETY: This component does NOT:
 * - implement or duplicate policy logic
 * - independently decide whether recovery is allowed
 * - interpret a failure category to authorize recovery
 * - override a backend policy decision
 * - call Razorpay directly
 * - call Ollama directly
 *
 * All recovery decisions come from the backend pipeline. Client-side
 * input clamping below only guards against malformed UI input (NaN,
 * negative numbers, absurdly large amounts) — it never changes what
 * the backend is allowed to do with the value.
 */

import { useEffect, useState } from "react";

import { getAuditLog, processPayment } from "../api/client";
import type {
  AuditRecord,
  DashboardResult,
  PaymentEventPayload,
} from "../types/dashboard";
import AuditLog from "./AuditLog";
import PipelineStages from "./PipelineStages";
import StatsBar from "./StatsBar";
import StatusBadge from "./StatusBadge";
import TopBar from "./TopBar";

/** Mirrors the backend's minimum (Section 4.1: amount >= 100 paise). */
const MIN_AMOUNT_PAISE = 100;
/** UI-only ceiling so a stray keystroke can't submit an absurd amount. */
const MAX_AMOUNT_PAISE = 10_000_000; // ₹1,00,000
const MIN_ATTEMPT = 1;
const MAX_ATTEMPT = 10;

/** Golden-path default: insufficient funds payment. */
function defaultPayload(): PaymentEventPayload {
  return {
    event_id: `evt_${Date.now()}`,
    razorpay_payment_id: `pay_test_${Date.now()}`,
    merchant_id: "merch_01",
    customer_id: "cust_001",
    type: "one_time",
    amount: 149900,
    currency: "INR",
    payment_method: "upi",
    error_code: "INSUFFICIENT_FUNDS",
    error_description: "Payment failed due to insufficient funds",
    failure_category: "unknown",
    attempt_number: 1,
    mandate_status: null,
    timestamp: new Date().toISOString(),
  };
}

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(Math.max(value, min), max);
}

export default function Dashboard() {
  const [result, setResult] = useState<DashboardResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [payload, setPayload] = useState<PaymentEventPayload>(defaultPayload);

  const [auditRecords, setAuditRecords] = useState<AuditRecord[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);

  async function fetchAuditLog() {
    setAuditLoading(true);
    setAuditError(null);
    try {
      const data = await getAuditLog();
      setAuditRecords(data.records);
    } catch (err) {
      setAuditError(
        err instanceof Error ? err.message : "Failed to load audit log",
      );
    } finally {
      setAuditLoading(false);
    }
  }

  useEffect(() => {
    fetchAuditLog();
  }, []);

  async function handleProcess() {
    if (loading) return; // guard against duplicate/overlapping submits
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const safeAmount = clamp(payload.amount, MIN_AMOUNT_PAISE, MAX_AMOUNT_PAISE);
      const safeAttempt = clamp(payload.attempt_number, MIN_ATTEMPT, MAX_ATTEMPT);
      const data = await processPayment({
        ...payload,
        amount: safeAmount,
        attempt_number: safeAttempt,
        // Generate fresh IDs each time
        event_id: `evt_${Date.now()}`,
        razorpay_payment_id: `pay_test_${Date.now()}`,
        timestamp: new Date().toISOString(),
      });
      setResult(data);
      // Keep the audit trail in sync with what the backend just recorded.
      fetchAuditLog();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to process payment",
      );
    } finally {
      setLoading(false);
    }
  }

  function handleAmountChange(raw: string) {
    const parsed = parseInt(raw, 10);
    setPayload((p) => ({
      ...p,
      amount: Number.isNaN(parsed)
        ? MIN_AMOUNT_PAISE
        : clamp(parsed, MIN_AMOUNT_PAISE, MAX_AMOUNT_PAISE),
    }));
  }

  function handleAttemptChange(raw: string) {
    const parsed = parseInt(raw, 10);
    setPayload((p) => ({
      ...p,
      attempt_number: Number.isNaN(parsed)
        ? MIN_ATTEMPT
        : clamp(parsed, MIN_ATTEMPT, MAX_ATTEMPT),
    }));
  }

  return (
    <div className="app-shell">
      <TopBar />
      <div className="dashboard">
        {/* Header */}
        <header className="dashboard-header">
          <h1>PayPulse Recovery Console</h1>
          <p>Operational view of the bounded payment recovery pipeline</p>
        </header>

        {/* Summary Stats */}
        <StatsBar records={auditRecords} />

        {/* Demo Payment Form */}
        <section className="card demo-form" data-testid="demo-form">
          <h2 className="card-title">Simulate a Failed Payment Event</h2>
          <div className="form-grid">
            <div className="form-field">
              <label htmlFor="error_code">Error Code</label>
              <select
                id="error_code"
                value={payload.error_code}
                onChange={(e) =>
                  setPayload((p) => ({ ...p, error_code: e.target.value }))
                }
              >
                <option value="INSUFFICIENT_FUNDS">INSUFFICIENT_FUNDS</option>
                <option value="EXPIRED_CARD">EXPIRED_CARD</option>
                <option value="GATEWAY_ERROR">GATEWAY_ERROR</option>
                <option value="BANK_DECLINED">BANK_DECLINED</option>
                <option value="AUTHENTICATION_ERROR">AUTHENTICATION_ERROR</option>
                <option value="SOMETHING_UNKNOWN">Unknown / Ambiguous</option>
              </select>
            </div>

            <div className="form-field">
              <label htmlFor="amount">Amount (paise)</label>
              <input
                id="amount"
                type="number"
                inputMode="numeric"
                min={MIN_AMOUNT_PAISE}
                max={MAX_AMOUNT_PAISE}
                value={payload.amount}
                onChange={(e) => handleAmountChange(e.target.value)}
              />
              <span className="form-hint">
                ₹{(MIN_AMOUNT_PAISE / 100).toFixed(2)} – ₹
                {(MAX_AMOUNT_PAISE / 100).toLocaleString("en-IN")}
              </span>
            </div>

            <div className="form-field">
              <label htmlFor="attempt">Attempt Number</label>
              <input
                id="attempt"
                type="number"
                inputMode="numeric"
                min={MIN_ATTEMPT}
                max={MAX_ATTEMPT}
                value={payload.attempt_number}
                onChange={(e) => handleAttemptChange(e.target.value)}
              />
              <span className="form-hint">
                Clamped to {MIN_ATTEMPT}–{MAX_ATTEMPT}
              </span>
            </div>

            <div className="form-field">
              <label htmlFor="payment_method">Payment Method</label>
              <select
                id="payment_method"
                value={payload.payment_method}
                onChange={(e) =>
                  setPayload((p) => ({
                    ...p,
                    payment_method: e.target.value as "upi" | "card" | "netbanking",
                  }))
                }
              >
                <option value="upi">UPI</option>
                <option value="card">Card</option>
                <option value="netbanking">Netbanking</option>
              </select>
            </div>
          </div>

          <button
            className="btn btn--primary"
            onClick={handleProcess}
            disabled={loading}
            data-testid="process-btn"
          >
            {loading ? "Processing…" : "Process Payment"}
          </button>
        </section>

        {/* Loading State */}
        {loading && (
          <div className="loading-state" data-testid="loading-state">
            <div className="spinner" />
            <div className="loading-state__title">Processing payment…</div>
            <div className="loading-state__description">
              Running through the recovery pipeline
            </div>
          </div>
        )}

        {/* Error State */}
        {error && !loading && (
          <div className="error-state" data-testid="error-state">
            <div className="error-state__icon">🚫</div>
            <div className="error-state__title">Backend Error</div>
            <div className="error-state__description">{error}</div>
          </div>
        )}

        {/* Result Display */}
        {result && !loading && (
          <div data-testid="result-display">
            {/* Outcome Banner */}
            <div className="outcome-banner">
              <div>
                <div className="outcome-banner__label">Recovery Outcome</div>
                <div className="outcome-banner__id">
                  {result.payment_id} · {result.event_id}
                </div>
              </div>
              <StatusBadge outcome={result.final_outcome} />
            </div>

            {/* Pipeline Stages */}
            <PipelineStages result={result} />
          </div>
        )}

        {/* Empty State */}
        {!result && !loading && !error && (
          <div className="empty-state" data-testid="empty-state">
            <div className="empty-state__icon">💳</div>
            <div className="empty-state__title">No payment processed yet</div>
            <div className="empty-state__description">
              Use the form above to send a payment event through the recovery
              pipeline. The result will appear here.
            </div>
          </div>
        )}

        {/* Audit Log */}
        <AuditLog
          records={auditRecords}
          loading={auditLoading}
          error={auditError}
          onRefresh={fetchAuditLog}
        />

        <footer className="app-footer">
          <p>
            PayPulse test-mode console — Rules decide. Qwen explains. The
            executor acts. The audit log records.
          </p>
        </footer>
      </div>
    </div>
  );
}
