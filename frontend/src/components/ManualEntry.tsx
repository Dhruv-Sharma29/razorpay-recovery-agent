/**
 * Manual event entry.
 *
 * Lets an operator craft an arbitrary failed payment and push it through
 * the pipeline, rather than only running presets. Amounts are entered in
 * rupees and converted to paise at submit — paise stays the wire unit.
 *
 * No policy logic lives here: the form only builds an event. Whether it
 * can be recovered is decided entirely by the backend.
 */

import { useState } from "react";

import type { PaymentEventPayload } from "../types/dashboard";

/** Mirrors the backend minimum (amount >= 100 paise). */
const MIN_RUPEES = 1;
/** UI-only ceiling so a stray keystroke cannot submit an absurd amount. */
const MAX_RUPEES = 100_000;
const MIN_ATTEMPT = 1;
const MAX_ATTEMPT = 10;

const ERROR_CODES = [
  "INSUFFICIENT_FUNDS",
  "EXPIRED_CARD",
  "GATEWAY_ERROR",
  "BANK_DECLINED",
  "AUTHENTICATION_ERROR",
  "INVOICE_OVERDUE",
  "SOMETHING_UNKNOWN",
];

const DESCRIPTIONS: Record<string, string> = {
  INSUFFICIENT_FUNDS: "Payment failed due to insufficient funds",
  EXPIRED_CARD: "The saved card or mandate has expired",
  GATEWAY_ERROR: "Gateway timed out before capture",
  BANK_DECLINED: "Card declined by the issuing bank",
  AUTHENTICATION_ERROR: "Customer did not complete authentication",
  INVOICE_OVERDUE: "Invoice is past its due date",
  SOMETHING_UNKNOWN: "Something went wrong that matches no known category",
};

/**
 * Unique per call, not per millisecond. Two rapid submissions must not
 * collide, or the backend treats the second as a duplicate event.
 */
function freshId(prefix: string): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
      : Math.random().toString(36).slice(2, 14);
  return `${prefix}_${Date.now().toString(36)}${rand}`;
}

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(Math.max(Math.round(value), min), max);
}

interface ManualEntryProps {
  disabled: boolean;
  onSubmit: (payload: PaymentEventPayload) => void;
}

export default function ManualEntry({ disabled, onSubmit }: ManualEntryProps) {
  const [errorCode, setErrorCode] = useState(ERROR_CODES[0]);
  const [rupees, setRupees] = useState(1499);
  const [attempt, setAttempt] = useState(1);
  const [method, setMethod] = useState<"upi" | "card" | "netbanking">("upi");
  const [txnType, setTxnType] = useState<"one_time" | "subscription">("one_time");
  const [mandate, setMandate] = useState<string>("none");
  // Live-gateway identifiers. Empty by default: a synthetic event has no real
  // mandate, and the live executor refuses rather than inventing one.
  const [rzpCustomer, setRzpCustomer] = useState("");
  const [rzpToken, setRzpToken] = useState("");
  const [rzpEmail, setRzpEmail] = useState("");
  const [rzpContact, setRzpContact] = useState("");
  const [showRzp, setShowRzp] = useState(false);

  // Not clamped while typing: an out-of-range value is flagged rather than
  // silently corrected, so the operator sees what they actually entered.
  const amountInvalid = rupees < MIN_RUPEES || rupees > MAX_RUPEES;
  const attemptInvalid = attempt < MIN_ATTEMPT || attempt > MAX_ATTEMPT;

  function submit() {
    onSubmit({
      event_id: freshId("evt_manual"),
      razorpay_payment_id: freshId("pay_manual"),
      merchant_id: "merch_manual",
      customer_id: "cust_manual",
      type: txnType,
      amount: clamp(rupees, MIN_RUPEES, MAX_RUPEES) * 100,
      currency: "INR",
      payment_method: method,
      error_code: errorCode,
      error_description: DESCRIPTIONS[errorCode] ?? "Manually entered failure",
      // The backend classifies from the error code; this is only the
      // event's self-reported label and is never trusted as input.
      failure_category: "unknown",
      attempt_number: clamp(attempt, MIN_ATTEMPT, MAX_ATTEMPT),
      mandate_status: mandate === "none" ? null : mandate,
      // Omitted entirely when blank, so an empty form stays indistinguishable
      // from an event that genuinely has no gateway context.
      razorpay:
        rzpCustomer || rzpToken || rzpEmail || rzpContact
          ? {
              customer_id: rzpCustomer || null,
              token_id: rzpToken || null,
              email: rzpEmail || null,
              contact: rzpContact || null,
            }
          : undefined,
      timestamp: new Date().toISOString(),
    });
  }

  return (
    <section className="card manual-entry" data-testid="manual-entry">
      <h2 className="card-title">Manual entry</h2>
      <p className="manual-entry__intro">
        Craft any failed payment and send it through the pipeline. The policy
        engine decides the outcome — nothing here can authorize a recovery.
      </p>

      <div className="manual-entry__grid">
        <div className="form-field">
          <label htmlFor="me-error-code">Error code</label>
          <select
            id="me-error-code"
            value={errorCode}
            disabled={disabled}
            onChange={(e) => setErrorCode(e.target.value)}
            data-testid="manual-error-code"
          >
            {ERROR_CODES.map((code) => (
              <option key={code} value={code}>
                {code === "SOMETHING_UNKNOWN"
                  ? "Unknown / ambiguous"
                  : code === "INVOICE_OVERDUE"
                    ? "INVOICE_OVERDUE (B2B receivable)"
                    : code}
              </option>
            ))}
          </select>
        </div>

        <div className={`form-field${amountInvalid ? " form-field--invalid" : ""}`}>
          <label htmlFor="me-amount">Amount (₹)</label>
          <input
            id="me-amount"
            type="number"
            inputMode="numeric"
            min={MIN_RUPEES}
            max={MAX_RUPEES}
            value={rupees}
            disabled={disabled}
            aria-invalid={amountInvalid}
            onChange={(e) => setRupees(parseInt(e.target.value, 10) || 0)}
            onBlur={() => setRupees(clamp(rupees, MIN_RUPEES, MAX_RUPEES))}
            data-testid="manual-amount"
          />
          <span className={`form-hint${amountInvalid ? " form-hint--error" : ""}`}>
            {amountInvalid
              ? `Must be between ₹${MIN_RUPEES} and ₹${MAX_RUPEES.toLocaleString("en-IN")}`
              : `₹${MIN_RUPEES} – ₹${MAX_RUPEES.toLocaleString("en-IN")} · the auto-recovery cap is ₹5,000`}
          </span>
        </div>

        <div className={`form-field${attemptInvalid ? " form-field--invalid" : ""}`}>
          <label htmlFor="me-attempt">Attempt number</label>
          <input
            id="me-attempt"
            type="number"
            inputMode="numeric"
            min={MIN_ATTEMPT}
            max={MAX_ATTEMPT}
            value={attempt}
            disabled={disabled}
            aria-invalid={attemptInvalid}
            onChange={(e) => setAttempt(parseInt(e.target.value, 10) || 0)}
            onBlur={() => setAttempt(clamp(attempt, MIN_ATTEMPT, MAX_ATTEMPT))}
            data-testid="manual-attempt"
          />
          <span className={`form-hint${attemptInvalid ? " form-hint--error" : ""}`}>
            {attemptInvalid
              ? `Must be between ${MIN_ATTEMPT} and ${MAX_ATTEMPT}`
              : "Above 3 trips the global hard cap"}
          </span>
        </div>

        <div className="form-field">
          <label htmlFor="me-method">Payment method</label>
          <select
            id="me-method"
            value={method}
            disabled={disabled}
            onChange={(e) =>
              setMethod(e.target.value as "upi" | "card" | "netbanking")
            }
            data-testid="manual-method"
          >
            <option value="upi">UPI</option>
            <option value="card">Card</option>
            <option value="netbanking">Netbanking</option>
          </select>
        </div>

        <div className="form-field">
          <label htmlFor="me-type">Transaction type</label>
          <select
            id="me-type"
            value={txnType}
            disabled={disabled}
            onChange={(e) => {
              const next = e.target.value as "one_time" | "subscription";
              setTxnType(next);
              if (next === "one_time") setMandate("none");
            }}
            data-testid="manual-type"
          >
            <option value="one_time">One-time</option>
            <option value="subscription">Subscription</option>
          </select>
        </div>

        <div className="form-field">
          <label htmlFor="me-mandate">Mandate status</label>
          <select
            id="me-mandate"
            value={mandate}
            disabled={disabled || txnType === "one_time"}
            onChange={(e) => setMandate(e.target.value)}
            data-testid="manual-mandate"
          >
            <option value="none">None</option>
            <option value="active">Active</option>
            <option value="expired">Expired</option>
            <option value="paused">Paused</option>
          </select>
          <span className="form-hint">
            {txnType === "one_time"
              ? "Only applies to subscriptions"
              : "Expired or paused needs re-authorization"}
          </span>
        </div>
      </div>

      <div className="manual-entry__rzp">
        <button
          type="button"
          className="btn btn--ghost"
          aria-expanded={showRzp}
          onClick={() => setShowRzp((open) => !open)}
          data-testid="manual-rzp-toggle"
        >
          Razorpay identifiers (optional)
        </button>
        {showRzp && (
          <>
            <p className="form-hint">
              Only used when EXECUTOR_MODE=razorpay_test. A retry needs a saved
              mandate; a payment link needs somewhere to send it. Leave blank
              and the executor reports that it could not attempt, rather than
              reporting a declined payment.
            </p>
            <div className="form-grid">
              <div className="form-field">
                <label htmlFor="me-rzp-customer">Customer id</label>
                <input
                  id="me-rzp-customer"
                  value={rzpCustomer}
                  disabled={disabled}
                  placeholder="cust_..."
                  onChange={(e) => setRzpCustomer(e.target.value)}
                  data-testid="manual-rzp-customer"
                />
              </div>
              <div className="form-field">
                <label htmlFor="me-rzp-token">Token id</label>
                <input
                  id="me-rzp-token"
                  value={rzpToken}
                  disabled={disabled}
                  placeholder="token_..."
                  onChange={(e) => setRzpToken(e.target.value)}
                  data-testid="manual-rzp-token"
                />
              </div>
              <div className="form-field">
                <label htmlFor="me-rzp-email">Email</label>
                <input
                  id="me-rzp-email"
                  type="email"
                  value={rzpEmail}
                  disabled={disabled}
                  placeholder="customer@example.com"
                  onChange={(e) => setRzpEmail(e.target.value)}
                  data-testid="manual-rzp-email"
                />
              </div>
              <div className="form-field">
                <label htmlFor="me-rzp-contact">Contact</label>
                <input
                  id="me-rzp-contact"
                  value={rzpContact}
                  disabled={disabled}
                  placeholder="+919999999999"
                  onChange={(e) => setRzpContact(e.target.value)}
                  data-testid="manual-rzp-contact"
                />
              </div>
            </div>
          </>
        )}
      </div>

      <button
        type="button"
        className="btn btn--primary"
        onClick={submit}
        disabled={disabled}
        data-testid="manual-submit"
      >
        {disabled && <span className="btn__spinner" aria-hidden="true" />}
        {disabled ? "Processing…" : "Process this event"}
      </button>
    </section>
  );
}
