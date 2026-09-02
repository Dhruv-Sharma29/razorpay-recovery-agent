/**
 * Dashboard component (TASK-013).
 *
 * Premium Fintech Redesign.
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

  // Derive outcome class for header styling safely
  const outcomeClass = result?.final_outcome === "recovered" ? "recovered" :
                       result?.final_outcome === "denied" ? "denied" :
                       result?.final_outcome === "escalated" ? "escalated" :
                       result?.final_outcome === "execution_failed" ? "failed" : "failed";

  // Keep the reasoning tab useful when Qwen is unavailable or an older backend
  // response does not include the new reasoning fields. These are display-only
  // fallbacks and never change the backend policy decision.
  const reasoningRecommendation = result?.reasoning_recommendation?.trim() ||
    (result?.policy_action
      ? `Follow policy decision: ${result.policy_action}`
      : "No recommendation available");
  const reasoningExplanation = result?.reasoning_explanation?.trim() ||
    result?.policy_reason ||
    "The policy engine did not return an explanation for this payment.";

  return (
    <div className="dashboard">
      {/* Header */}
      <header className="dashboard-header">
        <div className="header-left">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect width="24" height="24" rx="6" fill="var(--accent-primary)"/>
            <path d="M7 12L10 15L17 8" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <h1>Recovery Agent</h1>
          <p>Intelligent payment recovery operations</p>
        </div>
        <div className="header-right">
          <div className="system-status">
            <div className="status-dot" />
            System Operational
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <section className="hero-section">
        <div className="hero-content">
          <h2>Payment Recovery Console</h2>
          <p>Monitor, evaluate, and recover failed payments with deterministic safety controls.</p>
        </div>
        <div className="stats-grid">
          <div className="stat-card">
            <span className="stat-label">Processed</span>
            <span className="stat-value">—</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Recovered</span>
            <span className="stat-value">—</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Escalated</span>
            <span className="stat-value">—</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Denied</span>
            <span className="stat-value">—</span>
          </div>
        </div>
      </section>

      {/* Workspace */}
      <div className="workspace">
        
        {/* Left Column: Form */}
        <div className="workspace-col-left">
          <div className="card">
            <h3 className="card-title">Process a payment</h3>
            <div className="demo-form" data-testid="demo-form">
              <div className="form-row">
                <div className="form-field">
                  <label htmlFor="razorpay_payment_id">Payment ID</label>
                  <input
                    id="razorpay_payment_id"
                    type="text"
                    value={payload.razorpay_payment_id}
                    onChange={(e) =>
                      setPayload((p) => ({ ...p, razorpay_payment_id: e.target.value }))
                    }
                  />
                </div>
                <div className="form-field">
                  <label htmlFor="event_id">Event ID</label>
                  <input
                    id="event_id"
                    type="text"
                    value={payload.event_id}
                    onChange={(e) =>
                      setPayload((p) => ({ ...p, event_id: e.target.value }))
                    }
                  />
                </div>
              </div>

              <div className="form-row">
                <div className="form-field">
                  <label htmlFor="amount">Amount</label>
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
                  <label htmlFor="currency">Currency</label>
                  <select
                    id="currency"
                    value={payload.currency}
                    onChange={(e) =>
                      setPayload((p) => ({ ...p, currency: e.target.value }))
                    }
                  >
                    <option value="INR">INR</option>
                    <option value="USD">USD</option>
                  </select>
                </div>
              </div>

              <div className="form-row">
                <div className="form-field">
                  <label htmlFor="error_code">Failure Category</label>
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

              <div style={{ marginTop: '0.5rem' }}>
                <button
                  className="btn btn--primary"
                  onClick={handleProcess}
                  disabled={loading}
                  data-testid="process-btn"
                >
                  {loading ? "Analyzing…" : "Analyze Payment"}
                </button>
                <div className="safety-text">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                  </svg>
                  Decisions are governed by the deterministic Recovery Policy Engine.
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Result */}
        <div className="workspace-col-right">
          {/* Loading State */}
          {loading && (
            <div className="loading-state" data-testid="loading-state">
              <div className="spinner" />
              <div className="loading-state__title">Analyzing payment…</div>
              <div className="loading-state__description">
                Running through the recovery pipeline safely
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

          {/* Empty State */}
          {!result && !loading && !error && (
            <div className="empty-state" data-testid="empty-state">
              <div className="empty-state__icon">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" style={{color: 'var(--text-muted)'}}>
                  <rect x="2" y="5" width="20" height="14" rx="2"/>
                  <line x1="2" y1="10" x2="22" y2="10"/>
                </svg>
              </div>
              <div className="empty-state__title">Ready to analyze</div>
              <div className="empty-state__description">
                Submit a failed payment to view its recovery decision.
              </div>
            </div>
          )}

          {/* Result Display */}
          {result && !loading && (
            <div className="result-container" data-testid="result-display">
              {/* Outcome Header */}
              <div className={`outcome-header ${outcomeClass}`}>
                <h2 data-testid="status-badge" style={{ textTransform: "uppercase" }}>{result.final_outcome.replace("_", " ")}</h2>
                <div className="outcome-meta">
                  Payment: {result.payment_id} • Event: {result.event_id}
                </div>
              </div>

              {/* Pipeline Journey */}
              <PipelineStages result={result} />

              {/* Policy Decision Card */}
              <div className="card">
                <h3 className="section-title">Policy Decision</h3>
                <div className="info-grid" style={{ marginBottom: "1rem" }}>
                  <div className="info-box">
                    <div className="info-label">Automatic Recovery</div>
                    <div className="info-value">
                      {result.automatic_recovery_allowed === true ? (
                        <span style={{ color: "var(--status-recovered)" }}>AUTHORIZED</span>
                      ) : result.automatic_recovery_allowed === false ? (
                        <span style={{ color: "var(--status-failed)" }}>DENIED</span>
                      ) : (
                        "—"
                      )}
                    </div>
                  </div>
                  <div className="info-box">
                    <div className="info-label">Action</div>
                    <div className="info-value" style={{ textTransform: "capitalize" }}>
                      {result.policy_action ? result.policy_action.replace(/_/g, " ") : "—"}
                    </div>
                  </div>
                </div>
                <div className="info-box">
                  <div className="info-label">Reason</div>
                  <div className="info-sub">{result.policy_reason ?? "—"}</div>
                </div>
              </div>

              {/* Reasoning Card */}
              <div className="card reasoning-card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                  <h3 className="section-title" style={{ margin: 0, color: "var(--accent-hover)" }}>AI Reasoning</h3>
                  <span className="advisory-badge">
                    {result.reasoning_success === false ? "Policy-grounded fallback" : "Advisory — does not authorize recovery"}
                  </span>
                </div>
                <div className="info-box" style={{ background: "white", marginBottom: "1rem" }}>
                  <div className="info-label">Recommendation</div>
                  <div className="info-value" data-testid="reasoning-recommendation">{reasoningRecommendation}</div>
                </div>
                <div className="info-box" style={{ background: "white" }}>
                  <div className="info-label">Explanation</div>
                  <div className="info-sub" data-testid="reasoning-explanation">{reasoningExplanation}</div>
                </div>
              </div>

              {/* Escalation Card (Only if relevant) */}
              {result.escalation_status && result.escalation_status !== "not_required" && (
                <div className="card escalation-card">
                  <h3 className="section-title" style={{ color: "var(--status-escalated)" }}>Review Required</h3>
                  <div className="info-box" style={{ background: "white", marginBottom: "1rem" }}>
                    <div className="info-label">Severity</div>
                    <div className="info-value" style={{ textTransform: "uppercase" }}>{result.escalation_severity ?? "—"}</div>
                  </div>
                  <div className="info-box" style={{ background: "white" }}>
                    <div className="info-label">Reason</div>
                    <div className="info-sub">{result.escalation_reason ?? "—"}</div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Audit Log */}
      <AuditLog />
    </div>
  );
}
