/**
 * StatusBadge tests.
 *
 * SAFETY: the critical property is that no non-recovered outcome may
 * ever render as "Recovered".
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import StatusBadge from "../components/StatusBadge";
import type { FinalOutcome } from "../types/dashboard";

const CASES: [FinalOutcome, string][] = [
  ["recovered", "Recovered"],
  ["denied", "Denied"],
  ["escalated", "Escalated"],
  ["execution_failed", "Failed"],
  ["recorded", "Recorded"],
  ["audit_failed", "Audit Failed"],
];

describe("StatusBadge", () => {
  it.each(CASES)("renders %s as plain text '%s'", (outcome, label) => {
    render(<StatusBadge outcome={outcome} />);
    const badge = screen.getByTestId("status-badge");
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveClass(`status-badge--${outcome}`);
  });

  it("never labels a non-recovered outcome as Recovered", () => {
    for (const [outcome] of CASES.filter(([o]) => o !== "recovered")) {
      const { unmount } = render(<StatusBadge outcome={outcome} />);
      expect(screen.getByTestId("status-badge")).not.toHaveTextContent(
        /^Recovered$/,
      );
      unmount();
    }
  });

  it("falls back to Unknown for an unrecognised outcome", () => {
    render(<StatusBadge outcome={"something_new" as FinalOutcome} />);
    expect(screen.getByTestId("status-badge")).toHaveTextContent("Unknown");
  });
});
