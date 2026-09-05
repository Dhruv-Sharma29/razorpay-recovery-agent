/**
 * Agent view tests.
 *
 * The model is advisory. These pin that the view says so, labels model
 * output vs deterministic fallback, and explains a withheld customer
 * message rather than silently showing nothing.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DashboardResult } from "../types/dashboard";
import Agent from "../views/Agent";

function result(over: Partial<DashboardResult> = {}): DashboardResult {
  return {
    payment_id: "pay_1",
    event_id: "evt_1",
    failure_category: "network_error",
    classification_reason: "matched",
    policy_action: "immediate_retry",
    policy_reason: "retry once",
    automatic_recovery_allowed: true,
    reasoning_recommendation: "Retry",
    reasoning_explanation: "transient",
    reasoning_success: true,
    execution_status: "success",
    execution_reason: "done",
    escalation_status: "not_required",
    escalation_reason: "n/a",
    escalation_severity: null,
    final_outcome: "recovered",
    timestamp: "2026-09-03T00:00:00Z",
    amount: 149900,
    attempt_number: 1,
    error: null,
    payment_status: "captured",
    amount_recovered: 149900,
    simulated: true,
    reasoning_is_fallback: false,
    reasoning_model: "nvidia/nemotron-3-nano",
    root_cause_plain: "The gateway timed out.",
    why_appropriate: "A single retry is cheap and often works.",
    customer_message: "We could not complete your payment. Please try again.",
    escalation_summary: null,
    ...over,
  };
}

const noop = () => {};

const CASES = [
  { key: "recoverable", label: "Recoverable failure", tone: "primary" as const,
    build: () => ({}) as never },
  { key: "over-cap", label: "Over amount cap (₹90,000)", tone: "adversarial" as const,
    build: () => ({}) as never },
  { key: "retry-limit", label: "Past retry limit (attempt 5)", tone: "adversarial" as const,
    build: () => ({}) as never },
  { key: "unknown", label: "Unknown failure cause", tone: "adversarial" as const,
    build: () => ({}) as never },
];

describe("Agent", () => {
  it("states the advisory boundary up front", () => {
    render(<Agent result={null} loading={false} error={null} cases={CASES} onRunSample={noop} onRunGoldenPath={noop} />);
    expect(screen.getByText(/cannot authorize a payment/i)).toBeInTheDocument();
  });

  it("prompts for a sample before anything exists", () => {
    render(<Agent result={null} loading={false} error={null} cases={CASES} onRunSample={noop} onRunGoldenPath={noop} />);
    expect(screen.getByTestId("agent-empty")).toBeInTheDocument();
  });

  it("runs the recoverable sample on demand", () => {
    const onRunSample = vi.fn();
    render(
      <Agent result={null} loading={false} error={null} cases={CASES} onRunSample={onRunSample} onRunGoldenPath={noop} />,
    );
    fireEvent.click(screen.getByTestId("agent-case-recoverable"));
    expect(onRunSample).toHaveBeenCalledTimes(1);
  });

  it("offers the adversarial cases so refusals can be demonstrated", () => {
    const onRunSample = vi.fn();
    render(
      <Agent result={null} loading={false} error={null} cases={CASES} onRunSample={onRunSample} onRunGoldenPath={noop} />,
    );
    expect(screen.getByTestId("agent-case-over-cap")).toBeInTheDocument();
    expect(screen.getByTestId("agent-case-retry-limit")).toBeInTheDocument();
    expect(screen.getByTestId("agent-case-unknown")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("agent-case-over-cap"));
    expect(onRunSample).toHaveBeenCalledTimes(1);
  });

  it("badges model-generated output", () => {
    render(
      <Agent result={result()} loading={false} error={null} cases={CASES} onRunSample={noop} onRunGoldenPath={noop} />,
    );
    expect(screen.getByTestId("reasoning-source-badge")).toHaveTextContent(
      "Model generated",
    );
  });

  it("badges the deterministic fallback honestly", () => {
    render(
      <Agent
        result={result({ reasoning_is_fallback: true })}
        loading={false}
        error={null}
        cases={CASES} onRunSample={noop} onRunGoldenPath={noop}
      />,
    );
    expect(screen.getByTestId("reasoning-source-badge")).toHaveTextContent(
      "Deterministic fallback",
    );
  });

  it("shows the model's plain-language contribution", () => {
    render(
      <Agent result={result()} loading={false} error={null} cases={CASES} onRunSample={noop} onRunGoldenPath={noop} />,
    );
    expect(screen.getByText("The gateway timed out.")).toBeInTheDocument();
    expect(
      screen.getByText("A single retry is cheap and often works."),
    ).toBeInTheDocument();
  });

  it("explains why customer copy was withheld", () => {
    render(
      <Agent
        result={result({ customer_message: null })}
        loading={false}
        error={null}
        cases={CASES} onRunSample={noop} onRunGoldenPath={noop}
      />,
    );
    expect(
      screen.getByText(/withheld when it names amounts or promises/i),
    ).toBeInTheDocument();
  });

  it("reports the measured amount as simulated", () => {
    render(
      <Agent result={result()} loading={false} error={null} cases={CASES} onRunSample={noop} onRunGoldenPath={noop} />,
    );
    // The rail also shows the amount, so scope to the measured-result card.
    const measured = screen.getByText("Measured result").closest(".card");
    expect(measured).toHaveTextContent("₹1,499.00");
    expect(measured).toHaveTextContent(/captured \(simulated\)/);
  });

  it("surfaces a processing failure", () => {
    render(
      <Agent result={null} loading={false} error="boom" cases={CASES} onRunSample={noop} onRunGoldenPath={noop} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });
});
