/**
 * Reflow application shell.
 *
 * Batch-first: a sidebar picks the view, the top bar runs batches, and
 * every view renders backend-provided results. No policy logic lives
 * here — the UI only displays decisions the pipeline already made.
 */

import { useCallback, useEffect, useState } from "react";

import {
  getAuditLog,
  getProvider,
  getRisk,
  processPayment,
  resetState,
  runBatch,
} from "./api/client";
import Sidebar, { type ViewKey } from "./layout/Sidebar";
import TopBar from "./layout/TopBar";
import type {
  AuditRecord,
  BatchSummary,
  DashboardResult,
  PaymentEventPayload,
  ProviderStatus,
  RiskSummary,
} from "./types/dashboard";
import Agent from "./views/Agent";
import Cases from "./views/Cases";
import Overview from "./views/Overview";

/**
 * Payloads that deliberately trip a bounded stopping rule, so the demo can
 * show the agent refusing rather than only succeeding.
 *
 * Thresholds are the backend's: the auto-recovery cap is Rs 5,000 and the
 * global hard cap is 3 automated attempts.
 */
export const SAMPLE_CASES = [
  {
    key: "recoverable",
    label: "Recoverable failure",
    tone: "primary" as const,
    build: () => basePayload(),
  },
  {
    key: "over-cap",
    label: "Over amount cap (₹90,000)",
    tone: "adversarial" as const,
    build: () => ({
      ...basePayload(),
      amount: 90_000 * 100,
      error_code: "INSUFFICIENT_FUNDS",
      error_description: "Payment failed due to insufficient funds",
    }),
  },
  {
    key: "retry-limit",
    label: "Past retry limit (attempt 5)",
    tone: "adversarial" as const,
    build: () => ({
      ...basePayload(),
      attempt_number: 5,
      error_code: "INSUFFICIENT_FUNDS",
      error_description: "Payment failed due to insufficient funds",
    }),
  },
  {
    key: "unknown",
    label: "Unknown failure cause",
    tone: "adversarial" as const,
    build: () => ({
      ...basePayload(),
      error_code: "UNRECOGNIZED_ERROR_CODE",
      error_description: "Something that matches no known category",
    }),
  },
];

/** A representative failure for the Agent view's sample run. */
function basePayload(): PaymentEventPayload {
  // Unique per call: clicking two preset cases in the same millisecond must
  // not produce colliding event ids.
  const suffix = `${Date.now().toString(36)}${Math.random()
    .toString(36)
    .slice(2, 10)}`;
  return {
    event_id: `evt_agent_${suffix}`,
    razorpay_payment_id: `pay_agent_${suffix}`,
    merchant_id: "merch_01",
    customer_id: "cust_001",
    type: "one_time",
    amount: 149900,
    currency: "INR",
    payment_method: "upi",
    error_code: "GATEWAY_ERROR",
    error_description: "Gateway timed out before capture",
    failure_category: "unknown",
    attempt_number: 1,
    mandate_status: null,
    timestamp: new Date().toISOString(),
  };
}

export default function App() {
  const [view, setView] = useState<ViewKey>("overview");

  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<{ count: number; seconds: number } | null>(
    null,
  );

  const [risk, setRisk] = useState<RiskSummary | null>(null);
  const [provider, setProvider] = useState<ProviderStatus | null>(null);
  /** Last observed explanation source, from a sample or a batch. */
  const [lastWasFallback, setLastWasFallback] = useState<boolean | null>(null);
  const [records, setRecords] = useState<AuditRecord[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);

  const [sample, setSample] = useState<DashboardResult | null>(null);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [sampleError, setSampleError] = useState<string | null>(null);

  const refreshRisk = useCallback(async () => {
    try {
      setRisk(await getRisk());
    } catch {
      // The risk card is supplementary; a failure here must not take down
      // the batch view, so it simply stays empty.
      setRisk(null);
    }
  }, []);

  const refreshAudit = useCallback(async () => {
    setAuditLoading(true);
    setAuditError(null);
    try {
      const data = await getAuditLog();
      setRecords(data.records);
    } catch (err) {
      setAuditError(
        err instanceof Error ? err.message : "Failed to load the audit log",
      );
    } finally {
      setAuditLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAudit();
    refreshRisk();
    // Ask the backend which provider is configured, so the status pill is
    // truthful before anything has been processed.
    getProvider()
      .then(setProvider)
      .catch(() => setProvider(null));
  }, [refreshAudit, refreshRisk]);

  async function handleRun(
    count: number,
    runScheduler: boolean,
    explain: boolean,
  ) {
    if (running) return;
    setRunning(true);
    setBatchError(null);
    try {
      const data = await runBatch(count, { runScheduler, explain });
      setSummary(data);
      if (data.reasoning) {
        setLastWasFallback(
          data.reasoning.mode === "skipped" ||
            data.reasoning.model_generated === 0,
        );
      }
      setLastRun({
        count: data.transactions_processed,
        seconds: data.duration_seconds,
      });
      refreshAudit();
      refreshRisk();
    } catch (err) {
      setBatchError(
        err instanceof Error ? err.message : "Failed to run the batch",
      );
    } finally {
      setRunning(false);
    }
  }

  async function handleReset() {
    if (running) return;
    setRunning(true);
    setBatchError(null);
    try {
      await resetState();
      setSummary(null);
      setLastRun(null);
      refreshAudit();
      refreshRisk();
    } catch (err) {
      setBatchError(err instanceof Error ? err.message : "Failed to reset");
    } finally {
      setRunning(false);
    }
  }

  async function handleSample(payload: PaymentEventPayload) {
    if (sampleLoading) return;
    setSampleLoading(true);
    setSampleError(null);
    try {
      const data = await processPayment(payload);
      setSample(data);
      if (data.reasoning_is_fallback !== null &&
          data.reasoning_is_fallback !== undefined) {
        setLastWasFallback(data.reasoning_is_fallback);
      }
      refreshAudit();
      refreshRisk();
    } catch (err) {
      setSampleError(
        err instanceof Error ? err.message : "Failed to process the sample",
      );
    } finally {
      setSampleLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <Sidebar active={view} onNavigate={setView} />

      <div className="app-main">
        <TopBar
          providerModel={provider?.model ?? sample?.reasoning_model ?? null}
          providerConfigured={provider?.configured ?? null}
          lastWasFallback={lastWasFallback}
          running={running}
          onRun={handleRun}
          onReset={handleReset}
          lastRunSeconds={lastRun?.seconds ?? null}
          lastRunCount={lastRun?.count ?? null}
        />

        <main className="app-content">
          {view === "overview" && (
            <Overview
              summary={summary}
              risk={risk}
              error={batchError}
              running={running}
            />
          )}
          {view === "cases" && (
            <Cases
              records={records}
              loading={auditLoading}
              error={auditError}
              onRefresh={refreshAudit}
            />
          )}
          {view === "agent" && (
            <Agent
              result={sample}
              loading={sampleLoading}
              error={sampleError}
              cases={SAMPLE_CASES}
              onRunSample={handleSample}
            />
          )}
        </main>
      </div>
    </div>
  );
}
