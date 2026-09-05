/**
 * The two panels that make the model's contribution visible.
 *
 * The learning panel must never invent a prior it hasn't measured, and the
 * A/B must never present an inconclusive run as a result — those are the
 * ways a demo dashboard becomes dishonest.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AbPanel from "../components/AbPanel";
import AgentLearning from "../components/AgentLearning";
import type { AbResult, LearnedOutcomes } from "../types/dashboard";

const LEARNED: LearnedOutcomes = {
  min_observations: 3,
  learned: true,
  rows: [
    {
      category: "bank_decline",
      action: "switch_payment_method",
      recovered: 2,
      attempts: 5,
      success_rate: 0.4,
    },
    {
      category: "insufficient_funds",
      action: "scheduled_retry",
      recovered: 4,
      attempts: 5,
      success_rate: 0.8,
    },
  ],
};

function abResult(over: Partial<AbResult> = {}): AbResult {
  return {
    count_per_arm: 30,
    seed: 11,
    control: {
      label: "policy default action",
      recovered_amount: 1436200,
      recovery_rate_of_recoverable: 0.67,
      median_seconds_to_recovery: 0,
    },
    treatment: {
      label: "advisor chose among permitted actions",
      recovered_amount: 1366600,
      recovery_rate_of_recoverable: 0.64,
      median_seconds_to_recovery: 43200,
      actions_chosen_by_model: 3,
    },
    delta: {
      recovered_amount: -69600,
      recovery_rate_of_recoverable: -0.03,
      median_seconds_to_recovery: 43200,
    },
    conclusive: true,
    ...over,
  };
}

describe("AgentLearning", () => {
  it("renders nothing before the first fetch", () => {
    const { container } = render(<AgentLearning learned={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says it has learned nothing rather than inventing a prior", () => {
    render(
      <AgentLearning
        learned={{ min_observations: 3, learned: false, rows: [] }}
      />,
    );
    expect(screen.getByTestId("learning-empty")).toBeInTheDocument();
  });

  it("shows the measured rate and the sample it rests on", () => {
    render(<AgentLearning learned={LEARNED} />);
    const row = screen.getByTestId(
      "learning-insufficient_funds-scheduled_retry",
    );
    expect(row).toHaveTextContent("80%");
    expect(row).toHaveTextContent("4/5");
  });

  it("ranks the most effective action first", () => {
    render(<AgentLearning learned={LEARNED} />);
    const rates = screen
      .getAllByText(/^\d+%$/)
      .map((n) => n.textContent);
    expect(rates).toEqual(["80%", "40%"]);
  });
});

describe("AbPanel", () => {
  it("runs the comparison on demand", () => {
    const onRun = vi.fn();
    render(
      <AbPanel result={null} running={false} error={null} onRun={onRun} />,
    );
    fireEvent.click(screen.getByTestId("run-ab-btn"));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("reports a negative result as a negative result", () => {
    render(
      <AbPanel result={abResult()} running={false} error={null} onRun={vi.fn()} />,
    );
    const delta = screen.getByTestId("ab-delta");
    expect(delta).toHaveTextContent("−₹696.00");
    expect(delta).toHaveTextContent("-3pp");
  });

  it("marks a regression so it cannot read as an improvement", () => {
    render(
      <AbPanel result={abResult()} running={false} error={null} onRun={vi.fn()} />,
    );
    const cells = screen
      .getByTestId("ab-delta")
      .querySelectorAll('[data-tone="down"]');
    expect(cells.length).toBeGreaterThan(0);
  });

  it("never presents an inconclusive run as a finding", () => {
    render(
      <AbPanel
        result={abResult({ conclusive: false, note: "No choices were made." })}
        running={false}
        error={null}
        onRun={vi.fn()}
      />,
    );
    expect(screen.getByTestId("ab-inconclusive")).toHaveTextContent(
      "No choices were made.",
    );
    expect(screen.queryByTestId("ab-conclusive")).not.toBeInTheDocument();
  });

  it("says how many actions the advisor actually changed", () => {
    render(
      <AbPanel result={abResult()} running={false} error={null} onRun={vi.fn()} />,
    );
    expect(screen.getByTestId("ab-conclusive")).toHaveTextContent("3");
  });

  it("disables the control while measuring", () => {
    render(<AbPanel result={null} running error={null} onRun={vi.fn()} />);
    expect(screen.getByTestId("run-ab-btn")).toBeDisabled();
  });
});
