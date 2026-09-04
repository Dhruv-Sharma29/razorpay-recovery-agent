/**
 * Batch runner tests.
 *
 * The control must be safely repeatable: no double-submits while a run is
 * in flight, and reset must not imply audit history is discarded.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import BatchRunner from "../components/BatchRunner";

function setup(over: Partial<React.ComponentProps<typeof BatchRunner>> = {}) {
  const props = {
    running: false,
    onRun: vi.fn(),
    onReset: vi.fn(),
    lastRunSeconds: null,
    lastRunCount: null,
    ...over,
  };
  render(<BatchRunner {...props} />);
  return props;
}

describe("BatchRunner", () => {
  it("defaults to 25 transactions", () => {
    setup();
    expect(screen.getByTestId("batch-count")).toHaveValue(25);
  });

  it("runs the requested count", () => {
    const { onRun } = setup();
    fireEvent.change(screen.getByTestId("batch-count"), {
      target: { value: "40" },
    });
    fireEvent.click(screen.getByTestId("run-batch-btn"));
    expect(onRun).toHaveBeenCalledWith(40, true);
  });

  describe("typing the count", () => {
    // Regression: clamping on every keystroke turned an empty field into 1,
    // so the next digit appended to it — 5 became 15, 25 became 125.
    it.each([
      ["5", 5],
      ["10", 10],
      ["25", 25],
      ["100", 100],
    ])("typing %s runs exactly %i", (typed, expected) => {
      const { onRun } = setup();
      const input = screen.getByTestId("batch-count");
      fireEvent.change(input, { target: { value: "" } });
      fireEvent.change(input, { target: { value: typed } });

      expect(input).toHaveValue(expected);
      fireEvent.click(screen.getByTestId("run-batch-btn"));
      expect(onRun).toHaveBeenCalledWith(expected, true);
    });

    it("lets the field be emptied without snapping back to 1", () => {
      setup();
      const input = screen.getByTestId("batch-count");
      fireEvent.change(input, { target: { value: "" } });
      // The old bug refilled this with 1, which is what corrupted typing.
      expect(input).toHaveValue(null);
    });

    it("normalises an emptied field on blur", () => {
      setup();
      const input = screen.getByTestId("batch-count");
      fireEvent.change(input, { target: { value: "" } });
      fireEvent.blur(input);
      expect(input).toHaveValue(1);
    });

    it("replacing the whole value works", () => {
      const { onRun } = setup();
      const input = screen.getByTestId("batch-count");
      fireEvent.change(input, { target: { value: "40" } });
      fireEvent.click(screen.getByTestId("run-batch-btn"));
      expect(onRun).toHaveBeenCalledWith(40, true);
    });
  });

  it("clamps a count above the server limit", () => {
    const { onRun } = setup();
    fireEvent.change(screen.getByTestId("batch-count"), {
      target: { value: "9999" },
    });
    fireEvent.click(screen.getByTestId("run-batch-btn"));
    expect(onRun).toHaveBeenCalledWith(500, true);
  });

  it("clamps a count below one", () => {
    const { onRun } = setup();
    fireEvent.change(screen.getByTestId("batch-count"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByTestId("run-batch-btn"));
    expect(onRun).toHaveBeenCalledWith(1, true);
  });

  it("blocks a second submit while a run is in flight", () => {
    setup({ running: true });
    expect(screen.getByTestId("run-batch-btn")).toBeDisabled();
    expect(screen.getByTestId("batch-count")).toBeDisabled();
  });

  it("reports how long the last run took", () => {
    setup({ lastRunSeconds: 0.42, lastRunCount: 40 });
    expect(screen.getByRole("status")).toHaveTextContent("Ran 40 in 0.4s");
  });

  it("can skip the scheduler so deferred money stays uncounted", () => {
    const { onRun } = setup();
    fireEvent.click(screen.getByTestId("run-options-toggle"));
    fireEvent.click(screen.getByTestId("run-scheduler-toggle"));
    fireEvent.click(screen.getByTestId("run-batch-btn"));
    expect(onRun).toHaveBeenCalledWith(25, false);
  });

  it("says audit history survives a reset", () => {
    const { onReset } = setup();
    fireEvent.click(screen.getByTestId("run-options-toggle"));
    expect(
      screen.getByText(/append-only and is never cleared/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("reset-btn"));
    expect(onReset).toHaveBeenCalledTimes(1);
  });
});
