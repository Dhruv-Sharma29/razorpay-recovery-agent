/**
 * Dashboard component.
 *
 * Main Reflow console: a demo payment form, pipeline result display,
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
import { rupeesToPaise } from "../utils/currency";
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

/**
 * The form is denominated in rupees because that is what an operator
 * thinks in. Paise remains the wire unit: every payload is converted at
 * submit time, and the paise bounds above still backstop the request.
 */
const MIN_AMOUNT_RUPEES = MIN_AMOUNT_PAISE / 100; // ₹1
const MAX_AMOUNT_RUPEES = MAX_AMOUNT_PAISE / 100; // ₹1,00,000
const DEFAULT_AMOUNT_RUPEES = 1499;

/**
 * When the operator picks an error code, the description and category must
 * move with it. The backend classifier keys on error_code first and only
 * falls back to the description, so a stale description would otherwise
 * hijack classification — e.g. the ambiguous case, left with the default
 * "insufficient funds" description, would match the insufficient-funds
 * phrase and never read as unknown.
 */
interface ErrorCodePreset {
  description: string;
  category: PaymentEventPayload["failure_category"];
}

const ERROR_CODE_PRESETS: Record<string, ErrorCodePreset> = {
  INSUFFICIENT_FUNDS: {
    description: "Payment failed due to insufficient funds",
    category: "insufficient_funds",
  },
  EXPIRED_CARD: {
    description: "The card has expired",
    category: "expired_card",
  },
  GATEWAY_ERROR: {
    description:
      "Gateway timeout: the payment gateway did not respond in time",
    category: "network_error",
  },
  BANK_DECLINED: {
    description: "The payment was declined by the issuing bank",
    category: "bank_decline",
  },
  AUTHENTICATION_ERROR: {
    description: "Authentication failed during 3D Secure / OTP verification",
    category: "authentication_failure",
  },
  SOMETHING_UNKNOWN: {
    description: "Payment failed for an unspecified reason",
    category: "unknown",
  },
};

const MIN_ATTEMPT = 1;
const MAX_ATTEMPT = 10;

/** Stable unique id. Prefers a real UUID over a timestamp so rapid clicks
 * can't collide on the same millisecond. */
function genId(prefix: string): string {
  const uuid =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2);
  return `${prefix}_${uuid}`;
}

/** Golden-path default: insufficient funds payment. */
function defaultPayload(): PaymentEventPayload {
  return {
    event_id: genId("evt"),
    razorpay_payment_id: genId("pay_test"),
    merchant_id: "merch_01",
    customer_id: "cust_001",
    type: "one_time",
    amount: rupeesToPaise(DEFAULT_AMOUNT_RUPEES),
    currency: "INR",
    payment_method: "upi",
    error_code: "INSUFFICIENT_FUNDS",
    error_description: "Payment failed due to insufficient funds",
    failure_category: "insufficient_funds",
    attempt_number: 1,
    mandate_status: null,
    timestamp: new Date().toISOString(),
  };
}

/**
 * Payloads that deliberately trip a bounded stopping rule, so the demo
 * can show the agent refusing rather than only succeeding.
 *
 * Thresholds are the backend's, not the UI's: the amount cap is 500000
 * paise (₹5,000) and the global hard cap is 3 automated attempts.
 */
/** ₹90,000 — far above the ₹5,000 auto-recovery cap. */
const OVER_CAP_RUPEES = 90_000;

function overCapPayload(): PaymentEventPayload {
  return {
    ...defaultPayload(),
    amount: rupeesToPaise(OVER_CAP_RUPEES),
    error_code: "INSUFFICIENT_FUNDS",
    error_description: "Payment failed due to insufficient funds",
  };
}

function maxAttemptPayload(): PaymentEventPayload {
  return {
    ...defaultPayload(),
    attempt_number: 5, // above the 3-attempt hard cap
    error_code: "INSUFFICIENT_FUNDS",
    error_description: "Payment failed due to insufficient funds",
  };
}

function unknownCausePayload(): PaymentEventPayload {
  return {
    ...defaultPayload(),
    error_code: "UNRECOGNIZED_ERROR_CODE",
    error_description:
      "Something went wrong that doesn't match any known category",
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
  /** Held in rupees for the operator; converted to paise at submit. */
  const [amountRupees, setAmountRupees] = useState(DEFAULT_AMOUNT_RUPEES);

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

  async function runPipeline(event: PaymentEventPayload) {
    if (loading) return; // guard against duplicate/overlapping submits
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const safeAmount = clamp(event.amount, MIN_AMOUNT_PAISE, MAX_AMOUNT_PAISE);
      const safeAttempt = clamp(event.attempt_number, MIN_ATTEMPT, MAX_ATTEMPT);
      const data = await processPayment({
        ...event,
        amount: safeAmount,
        attempt_number: safeAttempt,
        // Generate fresh IDs each time
        event_id: genId("evt"),
        razorpay_payment_id: genId("pay_test"),
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

  function handleProcess() {
    // The form carries rupees; the wire carries paise.
    runPipeline({ ...payload, amount: rupeesToPaise(amountRupees) });
  }

  function handleAmountChange(raw: string) {
    const parsed = parseInt(raw, 10);
    setAmountRupees(
      Number.isNaN(parsed)
        ? MIN_AMOUNT_RUPEES
        : clamp(parsed, MIN_AMOUNT_RUPEES, MAX_AMOUNT_RUPEES),
    );
  }

  function handleErrorCodeChange(code: string) {
    const preset = ERROR_CODE_PRESETS[code];
    setPayload((p) => ({
      ...p,
      error_code: code,
      error_description: preset ? preset.description : p.error_description,
      failure_category: preset ? preset.category : p.failure_category,
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
          <h1>Reflow Recovery Console</h1>
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
                onChange={(e) => handleErrorCodeChange(e.target.value)}
              >
                <option value="INSUFFICIENT_FUNDS">INSUFFICIENT_FUNDS</option>
                <option value="EXPIRED_CARD">EXPIRED_CARD</option>
                <option value="GATEWAY_ERROR">GATEWAY_ERROR</option>
                <option value="BANK_DECLINED">BANK_DECLINED</option>
                <option value="AUTHENTICATION_ERROR">AUTHENTICATION_ERROR</option>
                <option value="SOMETHING_UNKNOWN">Unknown / Ambiguous</option>
              </select>
              <span className="form-hint">Razorpay-style failure code</span>
            </div>

            <div className="form-field">
              <label htmlFor="amount">Amount (₹)</label>
              <input
                id="amount"
                type="number"
                inputMode="numeric"
                min={MIN_AMOUNT_RUPEES}
                max={MAX_AMOUNT_RUPEES}
                value={amountRupees}
                onChange={(e) => handleAmountChange(e.target.value)}
              />
              <span className="form-hint">
                ₹{MIN_AMOUNT_RUPEES.toFixed(2)} – ₹
                {MAX_AMOUNT_RUPEES.toLocaleString("en-IN")}
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
              <span className="form-hint">Original payment rail</span>
            </div>
          </div>

          <div className="form-actions">
            <button
              className="btn btn--primary"
              onClick={handleProcess}
              disabled={loading}
              data-testid="process-btn"
            >
              {loading ? "Processing…" : "Process Payment"}
            </button>
          </div>

          {/* Adversarial cases: each trips a different bounded stopping
              rule, so the agent can be seen refusing on purpose. */}
          <div className="adversarial">
            <span className="adversarial__label">Try to break it</span>
            <div className="adversarial__actions">
              <button
                className="btn btn--adversarial"
                onClick={() => runPipeline(overCapPayload())}
                disabled={loading}
                data-testid="break-amount-btn"
              >
                Over amount cap (₹{OVER_CAP_RUPEES.toLocaleString("en-IN")})
              </button>
              <button
                className="btn btn--adversarial"
                onClick={() => runPipeline(maxAttemptPayload())}
                disabled={loading}
                data-testid="break-attempt-btn"
              >
                Past retry limit (attempt 5)
              </button>
              <button
                className="btn btn--adversarial"
                onClick={() => runPipeline(unknownCausePayload())}
                disabled={loading}
                data-testid="break-unknown-btn"
              >
                Unknown failure cause
              </button>
            </div>
          </div>
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
            <div className="error-state__icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <circle cx="12" cy="12" r="9" />
                <line x1="12" y1="7.5" x2="12" y2="13" strokeLinecap="round" />
                <line x1="12" y1="16.5" x2="12" y2="16.5" strokeLinecap="round" strokeWidth="2" />
              </svg>
            </div>
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
            <div className="empty-state__icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <rect x="2.5" y="5" width="19" height="14" rx="2.5" />
                <line x1="2.5" y1="9.5" x2="21.5" y2="9.5" />
                <line x1="6" y1="14.5" x2="10" y2="14.5" strokeLinecap="round" />
              </svg>
            </div>
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
            Reflow test-mode console — Rules decide. Nemotron explains. The
            executor acts. The audit log records.
          </p>
        </footer>
      </div>
    </div>
  );
}
