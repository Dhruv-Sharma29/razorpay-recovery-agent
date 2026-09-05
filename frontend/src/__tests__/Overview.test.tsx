/**
 * Overview tests.
 *
 * The KPI row is the brief's headline claim, so these pin that money is
 * reported as measured — and that the funnel narrows honestly.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BatchSummary } from "../types/dashboard";
import Overview from "../views/Overview";

const SUMMARY: BatchSummary = {
  transactions_processed: 40,
  total_attempted_amount: 43_426_500,
  total_recovered_amount: 1_204_200,
  recovery_rate_by_amount: 0.0277,
  total_recoverable_amount: 1_204_200,
  recovery_rate_of_recoverable: 1.0,
  recovery_rate_by_count: 0.125,
  outcomes: { escalated: 35, recovered: 5 },
  funnel: { raw: 40, needed_signal: 40, contacted: 5, confirmed_recovered: 5 },
  by_scenario: [
    {
      scenario: "insufficient_funds",
      count: 25,
      recovered_count: 5,
      attempted_amount: 25_123_700,
      recovered_amount: 1_204_200,
      recovery_rate_amount: 0.0479,
    },
  ],
  audit_ids: ["a1"],
  scheduler: {
    ran: 5, recovered: 5, failed: 0,
    amount_recovered: 1_204_200, job_ids: ["j1"],
  },
  simulated: true,
  duration_seconds: 0.4,
};

/** Scoped to the KPI row: "Confirmed recovered" also names a funnel stage. */
function tile(label: string) {
  const kpis = within(screen.getByTestId("kpi-tiles"));
  const el = kpis.getByText(label).closest(".stat-tile");
  if (!el) throw new Error(`no tile ${label}`);
  return within(el as HTMLElement);
}

describe("Overview", () => {
  it("prompts for a run before any batch exists", () => {
    render(<Overview summary={null} risk={null} error={null} running={false} />);
    expect(screen.getByTestId("overview-empty")).toBeInTheDocument();
  });

  it("reports revenue at risk and confirmed recovery", () => {
    render(<Overview summary={SUMMARY} risk={null} error={null} running={false} />);
    expect(tile("Revenue at risk").getByText("₹4.34L")).toBeInTheDocument();
    expect(tile("Confirmed recovered").getByText("₹12.0K")).toBeInTheDocument();
  });

  it("leads with the rate of what policy authorised chasing", () => {
    render(<Overview summary={SUMMARY} risk={null} error={null} running={false} />);
    // Recovered/recoverable measures the agent. Recovered/attempted (2.8%)
    // is dominated by cases policy correctly refused, so it is context, not
    // the headline.
    expect(tile("Recovery rate").getByText("100.0%")).toBeInTheDocument();
    expect(tile("Recovery rate").getByText(/2\.8% of everything that failed/)).toBeInTheDocument();
  });

  it("labels simulated results honestly", () => {
    render(<Overview summary={SUMMARY} risk={null} error={null} running={false} />);
    expect(screen.getByText(/payments are simulated/i)).toBeInTheDocument();
  });

  it("surfaces recommendation model telemetry", () => {
    render(
      <Overview
        summary={{
          ...SUMMARY,
          reasoning: {
            mode: "model",
            consultations: 40,
            model_generated: 40,
            fallback: 0,
            from_cache: 0,
            customer_messages: 30,
            overrode_policy: 0,
            model: "nvidia/nemotron-reasoner",
            average_latency_ms: 76,
          },
          recommendation: {
            mode: "model",
            consultations: 40,
            model_generated: 40,
            fallback: 0,
            risk_detected: 28,
            accepted: 20,
            constrained: 6,
            rejected: 2,
            unavailable: 12,
            model: "nvidia/nemotron-recommender",
            average_latency_ms: 91,
          },
        }}
        risk={null}
        error={null}
        running={false}
      />,
    );
    expect(screen.getByTestId("overview-ai-summary")).toHaveTextContent(
      "nvidia/nemotron-recommender",
    );
    expect(screen.getByTestId("ai-contribution")).toHaveTextContent(
      "91ms avg",
    );
  });

  it("renders each funnel stage with its count", () => {
    render(<Overview summary={SUMMARY} risk={null} error={null} running={false} />);
    const funnel = within(screen.getByTestId("recovery-funnel"));
    // Each stage appears twice: once as a bar, once in the definitions list.
    expect(funnel.getAllByText("Raw event-cases").length).toBeGreaterThan(0);
    expect(funnel.getByTestId("funnel-fill-confirmed_recovered")).toBeInTheDocument();
  });

  it("surfaces a batch failure", () => {
    render(<Overview summary={null} risk={null} error="backend down" running={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent("backend down");
  });

  it("shows outcome chips from the pipeline's own labels", () => {
    render(<Overview summary={SUMMARY} risk={null} error={null} running={false} />);
    const outcomes = within(screen.getByTestId("outcomes-bar"));
    expect(outcomes.getByText("Escalated")).toBeInTheDocument();
    expect(outcomes.getByText("Recovered")).toBeInTheDocument();
  });

  it("renders scenarios using the pipeline's real categories", () => {
    render(<Overview summary={SUMMARY} risk={null} error={null} running={false} />);
    const scenarios = within(screen.getByTestId("recovery-by-scenario"));
    expect(scenarios.getByText("Insufficient Funds")).toBeInTheDocument();
  });
});

describe("Honest contact reporting", () => {
  it("says how many interventions actually reached a customer", () => {
    const withOutreach = {
      ...SUMMARY,
      outreach: { attempted: 4, delivered: 3, simulated: true },
    };
    render(<Overview summary={withOutreach} risk={null} error={null} running={false} />);
    const line = screen.getByTestId("funnel-outreach");
    expect(line).toHaveTextContent("3");
    expect(line).toHaveTextContent(/withheld/);
    expect(line).toHaveTextContent(/silent retries against the payment rail/);
  });

  it("labels the stage Intervened rather than implying contact", () => {
    render(<Overview summary={SUMMARY} risk={null} error={null} running={false} />);
    const funnel = within(screen.getByTestId("recovery-funnel"));
    expect(funnel.getAllByText("Intervened").length).toBeGreaterThan(0);
  });

  it("omits the contact line when the batch reported no outreach", () => {
    render(<Overview summary={SUMMARY} risk={null} error={null} running={false} />);
    expect(screen.queryByTestId("funnel-outreach")).not.toBeInTheDocument();
  });
});

describe("Revenue at risk", () => {
  const RISK = {
    total_at_risk_amount: 10_000_00,
    total_recovered_amount: 2_000_00,
    outstanding_amount: 8_000_00,
    records_considered: 12,
    by_merchant: [
      {
        merchant_id: "merch_01",
        failures: 9,
        at_risk_amount: 8_000_00,
        recovered_amount: 2_000_00,
        outstanding_amount: 6_000_00,
      },
    ],
    repeat_customers: [
      { customer_ref: "cref_abc123", failures: 3, at_risk_amount: 3_000_00 },
    ],
    subscription_failures: {
      count: 4,
      at_risk_amount: 4_000_00,
      mandate_issues: 2,
    },
  };

  it("prompts for a run before anything is at risk", () => {
    render(<Overview summary={null} risk={null} error={null} running={false} />);
    expect(screen.getByTestId("revenue-at-risk")).toHaveTextContent(
      /Run a batch to build up an at-risk picture/i,
    );
  });

  it("leads with the outstanding amount", () => {
    render(<Overview summary={SUMMARY} risk={RISK} error={null} running={false} />);
    const card = within(screen.getByTestId("revenue-at-risk"));
    expect(card.getByText("₹8.0K")).toBeInTheDocument();
  });

  it("ranks the most exposed merchants", () => {
    render(<Overview summary={SUMMARY} risk={RISK} error={null} running={false} />);
    const card = within(screen.getByTestId("revenue-at-risk"));
    expect(card.getByText("merch_01")).toBeInTheDocument();
    expect(card.getByText("9 failures")).toBeInTheDocument();
  });

  it("groups repeat failures without exposing a customer id", () => {
    render(<Overview summary={SUMMARY} risk={RISK} error={null} running={false} />);
    const card = within(screen.getByTestId("revenue-at-risk"));
    expect(card.getByText("cref_abc123")).toBeInTheDocument();
    expect(card.getByText(/identifiers are never stored/i)).toBeInTheDocument();
  });

  it("explains why an expired mandate cannot just be retried", () => {
    render(<Overview summary={SUMMARY} risk={RISK} error={null} running={false} />);
    const card = within(screen.getByTestId("revenue-at-risk"));
    expect(card.getByText(/2 need re-authorization/)).toBeInTheDocument();
  });
});
