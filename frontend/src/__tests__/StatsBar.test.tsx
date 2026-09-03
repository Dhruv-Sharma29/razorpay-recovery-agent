/**
 * StatsBar tests.
 *
 * Covers the summary arithmetic and the rule that every KPI tile is
 * rendered with identical structure — including when a value is zero,
 * which must never read as "disabled".
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import StatsBar from "../components/StatsBar";
import type { AuditRecord, FinalOutcome } from "../types/dashboard";

function record(
  overrides: Partial<AuditRecord> & { final_outcome: FinalOutcome },
): AuditRecord {
  return {
    audit_id: `aud_${Math.random()}`,
    event_id: "evt_1",
    payment_id: "pay_1",
    timestamp: "2026-09-03T00:00:00Z",
    classification_category: "insufficient_funds",
    classification_reason: null,
    policy_action: null,
    policy_reason: null,
    automatic_recovery_allowed: null,
    reasoning_success: null,
    reasoning_reference: null,
    execution_status: null,
    execution_reference: null,
    escalation_status: null,
    escalation_reference: null,
    error: null,
    attempt_number: 1,
    amount: 100000,
    ...overrides,
  };
}

function tileValue(label: string): string {
  const tile = screen.getByText(label).closest(".stat-tile");
  if (!tile) throw new Error(`no tile for ${label}`);
  return within(tile as HTMLElement)
    .getByText(/.+/, { selector: ".stat-tile__value" })
    .textContent!.trim();
}

describe("StatsBar", () => {
  it("renders six KPI tiles", () => {
    const { container } = render(<StatsBar records={[]} />);
    expect(container.querySelectorAll(".stat-tile")).toHaveLength(6);
  });

  it("shows zeroes with a real value, not an empty/disabled tile", () => {
    render(<StatsBar records={[]} />);
    expect(tileValue("Recovery Rate")).toBe("0.0%");
    expect(tileValue("Total Processed")).toBe("0");
    expect(tileValue("Amount Recovered")).toBe("₹0.00");
  });

  it("computes outcome counts from audit records", () => {
    render(
      <StatsBar
        records={[
          record({ final_outcome: "recovered" }),
          record({ final_outcome: "recovered" }),
          record({ final_outcome: "escalated" }),
          record({ final_outcome: "execution_failed" }),
          record({ final_outcome: "audit_failed" }),
        ]}
      />,
    );
    expect(tileValue("Total Processed")).toBe("5");
    expect(tileValue("Recovered")).toBe("2");
    expect(tileValue("Escalated")).toBe("1");
    // execution_failed + audit_failed both count as exceptions
    expect(tileValue("Exceptions")).toBe("2");
  });

  it("sums only recovered amounts and derives the rate from them", () => {
    render(
      <StatsBar
        records={[
          record({ final_outcome: "recovered", amount: 150000 }),
          record({ final_outcome: "escalated", amount: 999900 }),
        ]}
      />,
    );
    expect(tileValue("Amount Recovered")).toBe("₹1,500.00");
    expect(tileValue("Recovery Rate")).toBe("50.0%");
  });

  it("omits the category chart until there is something to plot", () => {
    const { queryByTestId, rerender } = render(<StatsBar records={[]} />);
    expect(queryByTestId("category-chart")).not.toBeInTheDocument();

    rerender(<StatsBar records={[record({ final_outcome: "recovered" })]} />);
    expect(queryByTestId("category-chart")).toBeInTheDocument();
  });

  it("buckets records with no category under 'unknown'", () => {
    render(
      <StatsBar
        records={[
          record({ final_outcome: "recovered", classification_category: null }),
        ]}
      />,
    );
    expect(screen.getByTestId("category-chart")).toBeInTheDocument();
  });
});
