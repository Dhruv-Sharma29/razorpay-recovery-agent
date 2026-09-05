/**
 * Guided demo modal.
 *
 * The point of this modal is that a judge sees the real pipeline's decisions,
 * so the tests pin that every line is read from the response — including the
 * refusal path, where a demo that always showed success would be lying.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GuidedDemo from "../components/GuidedDemo";
import * as api from "../api/client";
import type { DashboardResult } from "../types/dashboard";

vi.mock("../api/client");

function result(over: Partial<DashboardResult> = {}): DashboardResult {
  return {
    payment_id: "pay_golden_1",
    event_id: "evt_golden_1",
    failure_category: "insufficient_funds",
    classification_reason: "Matched insufficient funds",
    ai_suggested_action: "scheduled_retry",
    policy_action: "scheduled_retry",
    automatic_recovery_allowed: true,
    policy_reason: "Retry permitted",
    reasoning_recommendation: "Wait for the account to be funded",
    reasoning_success: true,
    execution_status: "success",
    execution_reason: "Executed",
    escalation_status: null,
    escalation_reason: null,
    escalation_severity: null,
    amount_recovered: 499900,
    final_outcome: "recovery_executed",
    timestamp: "2026-09-01T00:00:00Z",
    amount: 499900,
    attempt_number: 1,
    error: null,
    ...over,
  } as DashboardResult;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("GuidedDemo", () => {
  it("renders nothing while closed", () => {
    const { container } = render(
      <GuidedDemo open={false} onClose={() => {}} onRecovered={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("is a labelled modal dialog", () => {
    render(<GuidedDemo open onClose={() => {}} onRecovered={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName(/guided demo/i);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<GuidedDemo open onClose={onClose} onRecovered={() => {}} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls the real golden-path pipeline endpoint", async () => {
    vi.mocked(api.runGoldenPath).mockResolvedValue(result());
    render(<GuidedDemo open onClose={() => {}} onRecovered={() => {}} />);
    fireEvent.click(screen.getByTestId("guided-demo-run"));
    await waitFor(() => expect(api.runGoldenPath).toHaveBeenCalledTimes(1));
  });

  it("logs each stage from the response", async () => {
    vi.mocked(api.runGoldenPath).mockResolvedValue(result());
    render(<GuidedDemo open onClose={() => {}} onRecovered={() => {}} />);
    fireEvent.click(screen.getByTestId("guided-demo-run"));

    for (const stage of [
      "classification",
      "recommendation",
      "policy",
      "reasoning",
      "execution",
      "audit",
    ]) {
      await waitFor(() =>
        expect(screen.getByTestId(`demo-log-${stage}`)).toBeInTheDocument(),
      );
    }
    expect(screen.getByTestId("demo-log-policy")).toHaveTextContent(
      /scheduled retry/i,
    );
  });

  it("reports a refusal as a refusal", async () => {
    // A demo that always showed success would misrepresent the policy engine.
    vi.mocked(api.runGoldenPath).mockResolvedValue(
      result({
        automatic_recovery_allowed: false,
        policy_action: "escalate",
        execution_status: "rejected",
        amount_recovered: 0,
      }),
    );
    render(<GuidedDemo open onClose={() => {}} onRecovered={() => {}} />);
    fireEvent.click(screen.getByTestId("guided-demo-run"));

    await waitFor(() =>
      expect(screen.getByTestId("demo-log-policy")).toHaveTextContent(
        /recovery refused/i,
      ),
    );
    expect(screen.getByTestId("demo-log-policy")).toHaveAttribute(
      "data-tone",
      "hold",
    );
  });

  it("announces the recovered amount when the run lands", async () => {
    const onRecovered = vi.fn();
    vi.mocked(api.runGoldenPath).mockResolvedValue(result());
    render(<GuidedDemo open onClose={() => {}} onRecovered={onRecovered} />);
    fireEvent.click(screen.getByTestId("guided-demo-run"));

    await waitFor(() => expect(onRecovered).toHaveBeenCalledTimes(1), {
      timeout: 3000,
    });
    expect(onRecovered.mock.calls[0][0]).toMatch(/4,999/);
  });

  it("does not claim a recovery when none happened", async () => {
    const onRecovered = vi.fn();
    vi.mocked(api.runGoldenPath).mockResolvedValue(
      result({ amount_recovered: 0 }),
    );
    render(<GuidedDemo open onClose={() => {}} onRecovered={onRecovered} />);
    fireEvent.click(screen.getByTestId("guided-demo-run"));

    await waitFor(() => expect(onRecovered).toHaveBeenCalledTimes(1), {
      timeout: 3000,
    });
    expect(onRecovered.mock.calls[0][0]).toMatch(/without recovering/i);
  });

  it("surfaces a backend failure instead of a fabricated run", async () => {
    vi.mocked(api.runGoldenPath).mockRejectedValue(new Error("backend down"));
    render(<GuidedDemo open onClose={() => {}} onRecovered={() => {}} />);
    fireEvent.click(screen.getByTestId("guided-demo-run"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("backend down"),
    );
    expect(screen.queryByTestId("guided-demo-log")).not.toBeInTheDocument();
  });
});
