/**
 * Dashboard component (TASK-009).
 *
 * Main dashboard with a demo payment form and pipeline result display.
 *
 * SAFETY: This component does NOT:
 * - implement or duplicate policy logic
 * - independently decide whether recovery is allowed
 * - interpret a failure category to authorize recovery
 * - override a backend policy decision
 * - call Razorpay directly
 * - call Ollama directly
 *
 * All recovery decisions come from the backend pipeline.
 */

import { useState } from "react";

import { processPayment } from "../api/client";
import type { DashboardResult, PaymentEventPayload } from "../types/dashboard";
import AuditLog from "./AuditLog";
import PipelineStages from "./PipelineStages";
import StatusBadge from "./StatusBadge";

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

export default function Dashboard() {
  const [result, setResult] = useState<DashboardResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [payload, setPayload] = useState<PaymentEventPayload>(defaultPayload);

  async function handleProcess() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await processPayment({
        ...payload,
        // Generate fresh IDs each time
        event_id: `evt_${Date.now()}`,
        razorpay_payment_id: `pay_test_${Date.now()}`,
        timestamp: new Date().toISOString(),
      });
      setResult(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to process payment",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="dashboard">
      {/* Header */}
      <header className="dashboard-header">
        <h1>Recovery Agent Dashboard</h1>
        <p>Operational view of the bounded payment recovery pipeline</p>
      </header>

      {/* Demo Payment Form */}
      <section className="card demo-form" data-testid="demo-form">
        <h2 className="card-title">Process Payment Event</h2>
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
              min={100}
              value={payload.amount}
              onChange={(e) =>
                setPayload((p) => ({
                  ...p,
                  amount: parseInt(e.target.value, 10) || 100,
                }))
              }
            />
          </div>

          <div className="form-field">
            <label htmlFor="attempt">Attempt Number</label>
            <input
              id="attempt"
              type="number"
              min={1}
              max={10}
              value={payload.attempt_number}
              onChange={(e) =>
                setPayload((p) => ({
                  ...p,
                  attempt_number: parseInt(e.target.value, 10) || 1,
                }))
              }
            />
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
      <AuditLog />
    </div>
  );
}
