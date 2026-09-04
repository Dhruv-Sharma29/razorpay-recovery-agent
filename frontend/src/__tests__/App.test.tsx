/**
 * App shell tests: view routing and the batch flow wiring.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import * as api from "../api/client";

vi.mock("../api/client", () => ({
  getAuditLog: vi.fn().mockResolvedValue({ records: [], count: 0, total: 0 }),
  getRisk: vi.fn().mockResolvedValue(null),
  getProvider: vi.fn().mockResolvedValue({
    provider: "nvidia-nim",
    model: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    base_url: "https://integrate.api.nvidia.com/v1",
    configured: false,
  }),
  processPayment: vi.fn(),
  runBatch: vi.fn(),
  resetState: vi.fn().mockResolvedValue({
    cleared: true,
    recovery_state_cleared: true,
    audit_log_preserved: true,
    note: "",
  }),
}));

const SUMMARY = {
  transactions_processed: 10,
  total_attempted_amount: 1_000_000,
  total_recovered_amount: 250_000,
  recovery_rate_by_amount: 0.25,
  recovery_rate_by_count: 0.2,
  outcomes: { recovered: 2, escalated: 8 },
  funnel: { raw: 10, needed_signal: 10, contacted: 2, confirmed_recovered: 2 },
  by_scenario: [],
  audit_ids: [],
  scheduler: null,
  simulated: true,
  duration_seconds: 0.3,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getAuditLog).mockResolvedValue({
    records: [],
    count: 0,
    total: 0,
  });
  // clearAllMocks keeps implementations, so restore the default explicitly
  // or a provider stub set by one test leaks into the next.
  vi.mocked(api.getProvider).mockResolvedValue({
    provider: "nvidia-nim",
    model: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    base_url: "https://integrate.api.nvidia.com/v1",
    configured: false,
  });
});

describe("App", () => {
  it("opens on the Overview", () => {
    render(<App />);
    expect(screen.getByTestId("view-overview")).toBeInTheDocument();
  });

  it("navigates between the three views", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-cases"));
    expect(screen.getByTestId("view-cases")).toBeInTheDocument();
    expect(screen.queryByTestId("view-overview")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("nav-agent"));
    expect(screen.getByTestId("view-agent")).toBeInTheDocument();
  });

  it("runs a batch and shows the measured result", async () => {
    vi.mocked(api.runBatch).mockResolvedValue(SUMMARY);
    render(<App />);

    fireEvent.click(screen.getByTestId("run-batch-btn"));

    await waitFor(() => {
      expect(screen.getByText("₹2.5K")).toBeInTheDocument();
    });
    expect(api.runBatch).toHaveBeenCalledWith(25, {
      runScheduler: true,
      explain: true,
    });
  });

  it("refreshes the audit log after a batch so Cases stays in sync", async () => {
    vi.mocked(api.runBatch).mockResolvedValue(SUMMARY);
    render(<App />);
    await waitFor(() => expect(api.getAuditLog).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId("run-batch-btn"));
    await waitFor(() => expect(api.getAuditLog).toHaveBeenCalledTimes(2));
  });

  it("surfaces a batch failure without crashing", async () => {
    vi.mocked(api.runBatch).mockRejectedValue(new Error("backend down"));
    render(<App />);

    fireEvent.click(screen.getByTestId("run-batch-btn"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("backend down");
    });
  });

  it("clears the shown batch on reset", async () => {
    vi.mocked(api.runBatch).mockResolvedValue(SUMMARY);
    render(<App />);
    fireEvent.click(screen.getByTestId("run-batch-btn"));
    await waitFor(() => expect(screen.getByText("₹2.5K")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("run-options-toggle"));
    fireEvent.click(screen.getByTestId("reset-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("overview-empty")).toBeInTheDocument();
    });
  });

  it("keeps the adversarial demo cases reachable from the Agent view", () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-agent"));
    expect(screen.getByTestId("agent-case-over-cap")).toBeInTheDocument();
    expect(screen.getByTestId("agent-case-retry-limit")).toBeInTheDocument();
    expect(screen.getByTestId("agent-case-unknown")).toBeInTheDocument();
  });

  it("does not claim a fallback before it knows anything", async () => {
    // Regression: the pill used to assert "deterministic fallback" on load,
    // even when a live provider was configured and working.
    vi.mocked(api.getProvider).mockImplementation(() => new Promise(() => {}));
    render(<App />);
    expect(screen.getByTestId("ai-status-pill")).toHaveTextContent(/checking/i);
  });

  it("names the configured provider once the backend reports one", async () => {
    vi.mocked(api.getProvider).mockResolvedValue({
      provider: "nvidia-nim",
      model: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
      base_url: "https://integrate.api.nvidia.com/v1",
      configured: true,
    });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("ai-status-pill")).toHaveTextContent(
        /nemotron-3/,
      );
    });
  });

  it("reports a fallback only when one is actually configured away", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("ai-status-pill")).toHaveTextContent(
        /deterministic fallback/i,
      );
    });
  });
});
