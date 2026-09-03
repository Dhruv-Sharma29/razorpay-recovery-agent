/**
 * AuditLog tests.
 *
 * The error state is operator-facing: it must offer a retry and stay
 * discoverable, not just print a developer message.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AuditLog from "../components/AuditLog";
import type { AuditRecord } from "../types/dashboard";

const RECORD: AuditRecord = {
  audit_id: "aud_1",
  event_id: "evt_1",
  payment_id: "pay_1",
  timestamp: "2026-09-03T00:00:00Z",
  classification_category: "insufficient_funds",
  classification_reason: null,
  policy_action: "scheduled_retry",
  policy_reason: null,
  automatic_recovery_allowed: true,
  reasoning_success: true,
  reasoning_reference: null,
  execution_status: "success",
  execution_reference: null,
  escalation_status: null,
  escalation_reference: null,
  final_outcome: "recovered",
  error: null,
  attempt_number: 1,
  amount: 149900,
};

const noop = () => {};

describe("AuditLog", () => {
  it("shows the empty state when there are no records", () => {
    render(<AuditLog records={[]} loading={false} error={null} onRefresh={noop} />);
    expect(screen.getByTestId("audit-empty")).toBeInTheDocument();
  });

  it("renders a row per record", () => {
    render(
      <AuditLog records={[RECORD]} loading={false} error={null} onRefresh={noop} />,
    );
    expect(screen.getByText("evt_1")).toBeInTheDocument();
    expect(screen.getByText("scheduled_retry")).toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toHaveTextContent("Recovered");
    expect(screen.queryByTestId("audit-empty")).not.toBeInTheDocument();
  });

  it("surfaces the failure and lets the operator retry via refresh", () => {
    const onRefresh = vi.fn();
    render(
      <AuditLog
        records={[]}
        loading={false}
        error="Could not reach the backend."
        onRefresh={onRefresh}
      />,
    );

    expect(screen.getByTestId("audit-error")).toBeInTheDocument();
    expect(screen.getByText("Could not reach the backend.")).toBeInTheDocument();

    // Refresh in the header is currently the only retry affordance.
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("suppresses the empty state while an error is showing", () => {
    render(
      <AuditLog records={[]} loading={false} error="boom" onRefresh={noop} />,
    );
    expect(screen.queryByTestId("audit-empty")).not.toBeInTheDocument();
  });

  it("disables refresh while loading", () => {
    render(<AuditLog records={[]} loading error={null} onRefresh={noop} />);
    expect(screen.getByRole("button", { name: /Loading/ })).toBeDisabled();
  });
});
