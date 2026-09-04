/**
 * Recovery Actions component tests.
 *
 * Verifies that all five executor-level counters render from the batch
 * response and that missing/null data is handled gracefully.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RecoveryActions from "../components/RecoveryActions";
import type { RecoveryActions as RecoveryActionsType } from "../types/dashboard";

function make(overrides: Partial<RecoveryActionsType> = {}): RecoveryActionsType {
  return {
    retries_attempted: 5,
    payments_recovered: 3,
    payments_pending: 1,
    payments_escalated: 2,
    execution_failed: 0,
    ...overrides,
  };
}

function card() {
  return within(screen.getByTestId("recovery-actions"));
}

describe("RecoveryActions", () => {
  it("renders nothing when actions is null", () => {
    const { container } = render(<RecoveryActions actions={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when actions is undefined", () => {
    const { container } = render(<RecoveryActions actions={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders all five counter tiles", () => {
    render(<RecoveryActions actions={make()} />);
    expect(card().getByText("Retries attempted")).toBeInTheDocument();
    expect(card().getByText("Payments recovered")).toBeInTheDocument();
    expect(card().getByText("Payments pending")).toBeInTheDocument();
    expect(card().getByText("Payments escalated")).toBeInTheDocument();
    expect(card().getByText("Execution failed")).toBeInTheDocument();
  });

  it("displays values from the batch response", () => {
    render(<RecoveryActions actions={make()} />);
    expect(screen.getByTestId("ra-retries-attempted")).toHaveTextContent("5");
    expect(screen.getByTestId("ra-payments-recovered")).toHaveTextContent("3");
    expect(screen.getByTestId("ra-payments-pending")).toHaveTextContent("1");
    expect(screen.getByTestId("ra-payments-escalated")).toHaveTextContent("2");
    expect(screen.getByTestId("ra-execution-failed")).toHaveTextContent("0");
  });

  it("handles all-zero counters", () => {
    render(
      <RecoveryActions
        actions={make({
          retries_attempted: 0,
          payments_recovered: 0,
          payments_pending: 0,
          payments_escalated: 0,
          execution_failed: 0,
        })}
      />,
    );
    expect(screen.getByTestId("ra-retries-attempted")).toHaveTextContent("0");
    expect(screen.getByTestId("ra-payments-recovered")).toHaveTextContent("0");
  });

  it("reads values from the batch rather than hardcoding", () => {
    render(
      <RecoveryActions
        actions={make({
          retries_attempted: 42,
          payments_recovered: 38,
          payments_pending: 2,
          payments_escalated: 7,
          execution_failed: 1,
        })}
      />,
    );
    expect(screen.getByTestId("ra-retries-attempted")).toHaveTextContent("42");
    expect(screen.getByTestId("ra-payments-recovered")).toHaveTextContent("38");
    expect(screen.getByTestId("ra-payments-pending")).toHaveTextContent("2");
    expect(screen.getByTestId("ra-payments-escalated")).toHaveTextContent("7");
    expect(screen.getByTestId("ra-execution-failed")).toHaveTextContent("1");
  });

  it("has the section title 'Recovery Actions'", () => {
    render(<RecoveryActions actions={make()} />);
    expect(card().getByText("Recovery Actions")).toBeInTheDocument();
  });
});
