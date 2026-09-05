/**
 * The restraint panel exists to stop escalations reading as failures, so
 * it must state the avoided cost and disappear when there was none.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Restraint from "../components/Restraint";
import type { RestraintSummary } from "../types/dashboard";

const RESTRAINT: RestraintSummary = {
  extra_attempts: 49,
  amount_chased_past_cap: 67936700,
  attempts_past_retry_cap: 3,
  blind_retries_on_unknown_cause: 2,
  non_retryable_retried: 1,
};

describe("Restraint", () => {
  it("names the attempts a naive agent would have burned", () => {
    render(<Restraint restraint={RESTRAINT} />);
    expect(screen.getByTestId("restraint-extra-attempts")).toHaveTextContent(
      "49",
    );
    expect(screen.getByTestId("restraint-past-cap")).toHaveTextContent("3");
    expect(screen.getByTestId("restraint-unknown")).toHaveTextContent("2");
    expect(screen.getByTestId("restraint-non-retryable")).toHaveTextContent(
      "1",
    );
  });

  it("shows the capped amount in rupees, not paise", () => {
    render(<Restraint restraint={RESTRAINT} />);
    expect(screen.getByTestId("restraint-amount").textContent).toMatch(
      /6,79,367/,
    );
  });

  it("renders nothing when the agent refused nothing", () => {
    const { container } = render(
      <Restraint restraint={{ ...RESTRAINT, extra_attempts: 0 }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing before a batch has run", () => {
    const { container } = render(<Restraint restraint={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
