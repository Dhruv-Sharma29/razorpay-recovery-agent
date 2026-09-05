/**
 * PipelineStages tests.
 *
 * The rail marks each stage reached/pending independently rather than
 * from a single "current stage" index, because stages get skipped — an
 * escalated payment never executes. These tests pin that behaviour, and
 * the advisory marking on the model stages.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PipelineStages from "../components/PipelineStages";
import type { DashboardResult } from "../types/dashboard";

function result(overrides: Partial<DashboardResult> = {}): DashboardResult {
  return {
    payment_id: "pay_1",
    event_id: "evt_1",
    failure_category: "insufficient_funds",
    classification_reason: "Matched rule",
    recommendation_success: true,
    revenue_at_risk: true,
    risk_score: 0.91,
    ai_suggested_action: "scheduled_retry",
    recommendation_status: "accepted",
    recommendation_reason: "Recommendation matched policy",
    policy_action: "scheduled_retry",
    policy_reason: "Retry after 24h",
    automatic_recovery_allowed: true,
    reasoning_recommendation: "Retry",
    reasoning_explanation: "Transient shortfall",
    reasoning_success: true,
    execution_status: "success",
    execution_reason: "Executed in sandbox",
    escalation_status: "not_required",
    escalation_reason: "Within limits",
    escalation_severity: null,
    final_outcome: "recovered",
    timestamp: "2026-09-03T00:00:00Z",
    amount: 149900,
    attempt_number: 1,
    error: null,
    ...overrides,
  };
}

function node(label: string): HTMLElement {
  const el = screen.getByText(label).closest(".rail-node");
  if (!el) throw new Error(`no rail node for ${label}`);
  return el as HTMLElement;
}

describe("PipelineStages", () => {
  it("renders all seven stages in pipeline order", () => {
    const { container } = render(<PipelineStages result={result()} />);
    const labels = [...container.querySelectorAll(".rail-node__label")].map((n) =>
      n.textContent?.replace(/Bounded|Advisory only/, "").trim(),
    );
    expect(labels).toEqual([
      "AI Recommendation",
      "Classification",
      "Policy Decision",
      "Reasoning",
      "Execution",
      "Escalation",
      "Transaction Info",
    ]);
  });

  it("distinguishes what the model may choose from what it may only say", () => {
    render(<PipelineStages result={result()} />);
    // The recommendation can become the action, but only from the set policy
    // already authorised — so it is bounded, not merely advisory.
    expect(node("AI Recommendation")).toHaveTextContent("Bounded");
    // Reasoning runs after the decision and only produces text.
    expect(node("Reasoning")).toHaveTextContent("Advisory only");
    // The deciding stage carries no model-authority badge at all.
    expect(node("Policy Decision")).not.toHaveTextContent("Advisory only");
    expect(node("Policy Decision")).not.toHaveTextContent("Bounded");
  });

  it("shows the AI suggestion separately from policy treatment", () => {
    render(<PipelineStages result={result()} />);
    expect(node("AI Recommendation")).toHaveTextContent("scheduled_retry");
    expect(node("AI Recommendation")).toHaveTextContent(
      "Policy treatment: accepted",
    );
  });

  it("marks a skipped stage pending and hides its detail", () => {
    // Escalated payments never execute.
    render(
      <PipelineStages
        result={result({
          execution_status: null,
          execution_reason: null,
          escalation_status: "open",
          escalation_severity: "high",
          final_outcome: "escalated",
        })}
      />,
    );
    const execution = node("Execution");
    expect(execution).toHaveClass("rail-node--pending");
    expect(execution.querySelector(".rail-node__detail")).toBeNull();

    const escalation = node("Escalation");
    expect(escalation).toHaveClass("rail-node--active");
    expect(escalation).toHaveTextContent("Severity: high");
  });

  it("keeps each backend field addressable rather than concatenated", () => {
    render(<PipelineStages result={result()} />);
    // policy_reason must stay its own text node
    expect(screen.getByText("Retry after 24h")).toBeInTheDocument();
    expect(screen.getByText("Recovery allowed: Yes")).toBeInTheDocument();
  });

  it("renders the amount and surfaces a pipeline error", () => {
    render(<PipelineStages result={result({ error: "boom" })} />);
    expect(screen.getByText("₹1,499.00")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });

  it("does not render an error region when there is no error", () => {
    render(<PipelineStages result={result()} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
