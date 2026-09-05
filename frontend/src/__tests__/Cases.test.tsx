/**
 * Cases tests: filtering, pagination, and the decision chain.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuditRecord } from "../types/dashboard";
import Cases from "../views/Cases";

function record(over: Partial<AuditRecord> = {}): AuditRecord {
  return {
    audit_id: `aud_${Math.random().toString(36).slice(2)}`,
    event_id: "evt_1",
    payment_id: "pay_1",
    timestamp: "2026-09-03T00:00:00Z",
    classification_category: "insufficient_funds",
    classification_reason: null,
    policy_action: "scheduled_retry",
    policy_reason: null,
    automatic_recovery_allowed: true,
    reasoning_success: true,
    reasoning_reference: null,
    execution_status: "scheduled",
    execution_reference: null,
    escalation_status: null,
    escalation_reference: null,
    final_outcome: "pending",
    error: null,
    attempt_number: 1,
    amount: 149900,
    classification_rule_id: "code.insufficient_funds",
    policy_rule_id: "policy.insufficient_funds.retry_24h",
    amount_limit: 500000,
    max_retries: 2,
    cooldown_seconds: 86400,
    scheduled_for: "2026-09-04T00:00:00Z",
    payment_status: "not_attempted",
    amount_recovered: 0,
    escalation_trigger: null,
    reasoning_is_fallback: true,
    ...over,
  };
}

const noop = () => {};

describe("Cases", () => {
  it("shows an empty state with no records", () => {
    render(<Cases records={[]} loading={false} error={null} onRefresh={noop} />);
    expect(screen.getByTestId("cases-empty")).toBeInTheDocument();
  });

  it("renders a row per record with its recovered amount", () => {
    render(
      <Cases
        records={[record({ payment_id: "pay_abc", amount_recovered: 149900 })]}
        loading={false}
        error={null}
        onRefresh={noop}
      />,
    );
    expect(screen.getByText("pay_abc")).toBeInTheDocument();
    expect(screen.getByText("₹1,499.00")).toBeInTheDocument();
  });

  it("filters by outcome", () => {
    render(
      <Cases
        records={[
          record({ payment_id: "pay_pending", final_outcome: "pending" }),
          record({ payment_id: "pay_recovered", final_outcome: "recovered" }),
        ]}
        loading={false}
        error={null}
        onRefresh={noop}
      />,
    );
    fireEvent.change(screen.getByTestId("filter-outcome"), {
      target: { value: "recovered" },
    });
    expect(screen.queryByText("pay_pending")).not.toBeInTheDocument();
    expect(screen.getByText("pay_recovered")).toBeInTheDocument();
  });

  it("filters by category", () => {
    render(
      <Cases
        records={[
          record({ payment_id: "pay_funds", classification_category: "insufficient_funds" }),
          record({ payment_id: "pay_network", classification_category: "network_error" }),
        ]}
        loading={false}
        error={null}
        onRefresh={noop}
      />,
    );
    fireEvent.change(screen.getByTestId("filter-category"), {
      target: { value: "network_error" },
    });
    expect(screen.queryByText("pay_funds")).not.toBeInTheDocument();
    expect(screen.getByText("pay_network")).toBeInTheDocument();
  });

  it("shows a live countdown for pending retries", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-03T23:59:00Z"));
    try {
      const rec = record({
        payment_id: "pay_countdown",
        scheduled_for: "2026-09-04T00:00:00Z",
      });
      render(<Cases records={[rec]} loading={false} error={null} onRefresh={noop} />);

      expect(screen.getByTestId(`retry-countdown-${rec.audit_id}`)).toHaveTextContent(
        "Retry in 1m 0s",
      );
      act(() => vi.advanceTimersByTime(60_000));
      expect(screen.getByTestId(`retry-countdown-${rec.audit_id}`)).toHaveTextContent(
        "Ready to retry",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("expands a row into the full decision chain", () => {
    const rec = record({ payment_id: "pay_chain" });
    render(
      <Cases records={[rec]} loading={false} error={null} onRefresh={noop} />,
    );
    fireEvent.click(screen.getByTestId(`expand-${rec.audit_id}`));

    const chain = within(screen.getByTestId(`chain-${rec.audit_id}`));
    expect(chain.getByText("Cause")).toBeInTheDocument();
    expect(chain.getByText("Rule")).toBeInTheDocument();
    expect(chain.getByText("Bound")).toBeInTheDocument();
    expect(chain.getByText("Outcome")).toBeInTheDocument();
    // The bound must show the real limits, not a placeholder.
    expect(chain.getByText(/cap ₹5,000.00/)).toBeInTheDocument();
    expect(chain.getByText(/max 2 retries/)).toBeInTheDocument();
    expect(chain.getByText(/24h cooldown/)).toBeInTheDocument();
  });

  it("shows recommendation history in the expanded chain", () => {
    const rec = record({
      recommendation_revenue_at_risk: true,
      recommendation_risk_score: 0.91,
      recommendation_suggested_cause: "insufficient_funds",
      recommendation_suggested_action: "scheduled_retry",
      recommendation_confidence: 0.94,
      recommendation_status: "accepted",
      recommendation_model: "nvidia/nemotron-test",
      recommendation_prompt_version: "1.0.0",
      recommendation_latency_ms: 83,
    });
    render(<Cases records={[rec]} loading={false} error={null} onRefresh={noop} />);
    fireEvent.click(screen.getByTestId(`expand-${rec.audit_id}`));

    const chain = within(screen.getByTestId(`chain-${rec.audit_id}`));
    expect(chain.getByText(/AI recommendation/)).toBeInTheDocument();
    expect(chain.getByText(/Scheduled Retry · Accepted/)).toBeInTheDocument();
    expect(chain.getByText(/risk 91% · confidence 94% · 83ms/)).toBeInTheDocument();
    expect(chain.getByText(/nvidia\/nemotron-test · prompt 1.0.0/)).toBeInTheDocument();
  });

  it("paginates beyond one page", () => {
    const many = Array.from({ length: 20 }, (_, i) =>
      record({ payment_id: `pay_${i}` }),
    );
    render(
      <Cases records={many} loading={false} error={null} onRefresh={noop} />,
    );
    expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument();
    expect(screen.queryByText("pay_19")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("pay_19")).toBeInTheDocument();
  });

  it("refreshes on demand", () => {
    const onRefresh = vi.fn();
    render(
      <Cases records={[]} loading={false} error={null} onRefresh={onRefresh} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("surfaces a load failure", () => {
    render(
      <Cases records={[]} loading={false} error="nope" onRefresh={noop} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("nope");
  });
});
