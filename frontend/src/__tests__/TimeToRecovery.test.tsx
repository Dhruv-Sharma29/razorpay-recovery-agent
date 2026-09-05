/**
 * Recovering money late is a different outcome from recovering it now, so
 * the panel has to make a cooldown visible rather than averaging it away.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import TimeToRecovery from "../components/TimeToRecovery";
import type { BatchTiming } from "../types/dashboard";

const TIMING: BatchTiming = {
  recovered_count: 6,
  median_seconds: 86399,
  max_seconds: 259200,
  instant_count: 2,
};

describe("TimeToRecovery", () => {
  it("renders nothing before a batch has run", () => {
    const { container } = render(<TimeToRecovery timing={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when nothing recovered", () => {
    const { container } = render(
      <TimeToRecovery
        timing={{ ...TIMING, recovered_count: 0, median_seconds: null }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("reports a cooldown in hours, not raw seconds", () => {
    render(<TimeToRecovery timing={TIMING} />);
    expect(screen.getByTestId("ttr-median")).toHaveTextContent("24.0h");
  });

  it("switches to days once hours stop being readable", () => {
    render(<TimeToRecovery timing={TIMING} />);
    expect(screen.getByTestId("ttr-max")).toHaveTextContent("3.0d");
  });

  it("separates immediate recoveries from deferred ones", () => {
    render(<TimeToRecovery timing={TIMING} />);
    expect(screen.getByTestId("ttr-instant")).toHaveTextContent("2");
    // 6 recovered, 2 inline, so 4 served a cooldown first.
    expect(screen.getByTestId("ttr-deferred")).toHaveTextContent("4");
  });

  it("shows seconds when recovery really was immediate", () => {
    render(
      <TimeToRecovery
        timing={{ ...TIMING, median_seconds: 0, max_seconds: 12 }}
      />,
    );
    expect(screen.getByTestId("ttr-median")).toHaveTextContent("0s");
    expect(screen.getByTestId("ttr-max")).toHaveTextContent("12s");
  });

  it("shows a dash rather than zero when a figure is unavailable", () => {
    render(<TimeToRecovery timing={{ ...TIMING, median_seconds: null }} />);
    expect(screen.getByTestId("ttr-median")).toHaveTextContent("—");
  });
});
