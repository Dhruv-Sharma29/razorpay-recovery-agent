/**
 * PipelineStages tests.
 *
 * The rail marks each stage reached/pending independently rather than
 * from a single "current stage" index, because stages get skipped — an
 * escalated payment never executes. These tests pin that behaviour, and
 * the advisory marking on the LLM stage.
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
  it("renders all six stages in pipeline order", () => {
    const { container } = render(<PipelineStages result={result()} />);
    const labels = [...container.querySelectorAll(".rail-node__label")].map((n) =>
      n.textContent?.replace("Advisory only", "").trim(),
    );
    expect(labels).toEqual([
      "Classification",
      "Policy Decision",
      "Reasoning",
      "Execution",
      "Escalation",
      "Transaction Info",
    ]);
  });

  it("marks the reasoning stage as advisory only", () => {
    render(<PipelineStages result={result()} />);
    expect(node("Reasoning")).toHaveTextContent("Advisory only");
    // The deciding stage must never be labelled advisory.
    expect(node("Policy Decision")).not.toHaveTextContent("Advisory only");
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
