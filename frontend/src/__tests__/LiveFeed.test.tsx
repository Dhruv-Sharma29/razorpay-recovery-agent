/**
 * The feed's job is to show a run in progress, so it must render each case
 * as it arrives and label the verdict correctly — a held case must never
 * look like a recovered one.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import LiveFeed from "../components/LiveFeed";
import type { BatchCaseFrame } from "../types/dashboard";

function frame(over: Partial<BatchCaseFrame> = {}): BatchCaseFrame {
  return {
    index: 1,
    total: 3,
    payment_id: "pay_one",
    amount: 264400,
    category: "insufficient_funds",
    action: "scheduled_retry",
    allowed: true,
    escalation_reason: null,
    recovered: false,
    outcome: "recovery_scheduled",
    ...over,
  };
}

describe("LiveFeed", () => {
  it("renders nothing before the first case arrives", () => {
    const { container } = render(<LiveFeed cases={[]} running />);
    expect(container).toBeEmptyDOMElement();
  });

  it("counts progress against the batch total", () => {
    render(
      <LiveFeed
        cases={[frame(), frame({ index: 2, payment_id: "pay_two" })]}
        running
      />,
    );
    expect(screen.getByTestId("live-feed-progress")).toHaveTextContent("2 / 3");
  });

  it("shows the newest case first", () => {
    render(
      <LiveFeed
        cases={[frame(), frame({ index: 2, payment_id: "pay_two" })]}
        running
      />,
    );
    const ids = screen
      .getAllByText(/^pay_/)
      .map((node) => node.textContent);
    expect(ids).toEqual(["pay_two", "pay_one"]);
  });

  it("labels a recovered case as recovered", () => {
    render(<LiveFeed cases={[frame({ recovered: true })]} running={false} />);
    expect(screen.getByText("Recovered")).toBeInTheDocument();
  });

  it("labels a refused case with why it was held, not as an action", () => {
    render(
      <LiveFeed
        cases={[
          frame({
            allowed: false,
            recovered: false,
            action: "escalate",
            escalation_reason: "amount_exceeds_limit",
          }),
        ]}
        running={false}
      />,
    );
    expect(screen.getByText(/amount exceeds limit/i)).toBeInTheDocument();
    expect(screen.queryByText("Recovered")).not.toBeInTheDocument();
  });

  it("caps the list so a large batch cannot grow without bound", () => {
    const many = Array.from({ length: 120 }, (_, i) =>
      frame({ index: i + 1, total: 120, payment_id: `pay_${i}` }),
    );
    render(<LiveFeed cases={many} running />);
    expect(screen.getAllByText(/^pay_/)).toHaveLength(40);
    // Progress still reflects the whole run, not just what is displayed.
    expect(screen.getByTestId("live-feed-progress")).toHaveTextContent(
      "120 / 120",
    );
  });

  it("announces that work is still running only while it is", () => {
    const { rerender } = render(<LiveFeed cases={[frame()]} running />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    rerender(<LiveFeed cases={[frame()]} running={false} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
