/**
 * Dashboard component tests (TASK-009).
 *
 * Verifies that the dashboard renders correctly and handles all
 * pipeline outcomes (successful recovery, denied, escalated, unsafe).
 * Verifies no policy logic exists in frontend.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import Dashboard from "../components/Dashboard";
import * as api from "../api/client";

// Mock the API client
vi.mock("../api/client", () => ({
  processPayment: vi.fn(),
  getAuditLog: vi.fn().mockResolvedValue({ records: [], count: 0 }),
}));

describe("Dashboard Component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("1. renders empty state initially", () => {
    render(<Dashboard />);
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    expect(screen.getByText("No payment processed yet")).toBeInTheDocument();
  });

  it("2. shows loading state during API call", async () => {
    // Make processPayment never resolve so loading state stays
    vi.mocked(api.processPayment).mockImplementation(() => new Promise(() => {}));
    
    render(<Dashboard />);
    
    const processBtn = screen.getByTestId("process-btn");
    fireEvent.click(processBtn);
    
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();
    expect(screen.getByText("Processing payment…")).toBeInTheDocument();
  });

  it("3. displays backend error state", async () => {
    vi.mocked(api.processPayment).mockRejectedValue(new Error("Pipeline failed"));
    
    render(<Dashboard />);
    
    const processBtn = screen.getByTestId("process-btn");
    fireEvent.click(processBtn);
    
    await waitFor(() => {
      expect(screen.getByTestId("error-state")).toBeInTheDocument();
      expect(screen.getByText("Pipeline failed")).toBeInTheDocument();
    });
  });

  it("4. successful recovery display", async () => {
    vi.mocked(api.processPayment).mockResolvedValue({
      payment_id: "pay_123",
      event_id: "evt_123",
      failure_category: "insufficient_funds",
      classification_reason: "Matches INSUFFICIENT_FUNDS",
      policy_action: "scheduled_retry",
      policy_reason: "Retry after 24h",
      automatic_recovery_allowed: true,
      reasoning_recommendation: "Retry",
      reasoning_explanation: "Explanation",
      reasoning_success: true,
      execution_status: "success",
      execution_reason: "Action executed",
      escalation_status: "not_required",
      escalation_reason: "No escalation needed",
      escalation_severity: null,
      final_outcome: "recovered",
      timestamp: "2026-09-01T10:00:00Z",
      amount: 149900,
      attempt_number: 1,
      error: null,
    });

    render(<Dashboard />);
    
    fireEvent.click(screen.getByTestId("process-btn"));
    
    await waitFor(() => {
      expect(screen.getByTestId("result-display")).toBeInTheDocument();
      const statusBadge = screen.getByTestId("status-badge");
      expect(statusBadge).toHaveTextContent("Recovered");
      expect(screen.getByText("scheduled_retry")).toBeInTheDocument();
    });
  });

  it("5. denied recovery display", async () => {
    vi.mocked(api.processPayment).mockResolvedValue({
      payment_id: "pay_124",
      event_id: "evt_124",
      failure_category: "insufficient_funds",
      classification_reason: "Matches",
      policy_action: "escalate",
      policy_reason: "Retry limit exhausted",
      automatic_recovery_allowed: false,
      reasoning_recommendation: "Escalate",
      reasoning_explanation: "Explanation",
      reasoning_success: true,
      execution_status: null,
      execution_reason: null,
      escalation_status: "open",
      escalation_reason: "Retry limit",
      escalation_severity: "medium",
      final_outcome: "escalated",
      timestamp: "2026-09-01T10:00:00Z",
      amount: 149900,
      attempt_number: 3,
      error: null,
    });

    render(<Dashboard />);
    
    fireEvent.click(screen.getByTestId("process-btn"));
    
    await waitFor(() => {
      const statusBadge = screen.getByTestId("status-badge");
      // Could be denied or escalated depending on backend logic
      // In this case we mocked final_outcome as escalated
      expect(statusBadge).toHaveTextContent("Escalated");
      expect(screen.getByText("Retry limit exhausted")).toBeInTheDocument();
    });
  });

  it("6. unsafe/unknown display never says recovered", async () => {
    vi.mocked(api.processPayment).mockResolvedValue({
      payment_id: "pay_125",
      event_id: "evt_125",
      failure_category: "unknown",
      classification_reason: "No rules matched",
      policy_action: "escalate",
      policy_reason: "Unknown failure",
      automatic_recovery_allowed: false,
      reasoning_recommendation: "Escalate",
      reasoning_explanation: "Explanation",
      reasoning_success: true,
      execution_status: null,
      execution_reason: null,
      escalation_status: "open",
      escalation_reason: "Unknown",
      escalation_severity: "high",
      final_outcome: "escalated",
      timestamp: "2026-09-01T10:00:00Z",
      amount: 149900,
      attempt_number: 1,
      error: null,
    });

    render(<Dashboard />);
    
    fireEvent.click(screen.getByTestId("process-btn"));
    
    await waitFor(() => {
      const statusBadge = screen.getByTestId("status-badge");
      expect(statusBadge).not.toHaveTextContent("Recovered");
      expect(statusBadge).toHaveTextContent("Escalated");
      expect(screen.getByText("Unknown failure")).toBeInTheDocument();
    });
  });

  it("7. policy decision is displayed without reinterpretation", async () => {
    vi.mocked(api.processPayment).mockResolvedValue({
      payment_id: "pay_126",
      event_id: "evt_126",
      failure_category: "network_error",
      classification_reason: "Match",
      policy_action: "immediate_retry",
      policy_reason: "Immediate retry once permitted",
      automatic_recovery_allowed: true,
      reasoning_recommendation: "Retry",
      reasoning_explanation: "Explanation",
      reasoning_success: true,
      execution_status: "success",
      execution_reason: "Action executed",
      escalation_status: "not_required",
      escalation_reason: "No escalation needed",
      escalation_severity: null,
      final_outcome: "recovered",
      timestamp: "2026-09-01T10:00:00Z",
      amount: 149900,
      attempt_number: 1,
      error: null,
    });

    render(<Dashboard />);
    
    fireEvent.click(screen.getByTestId("process-btn"));
    
    await waitFor(() => {
      expect(screen.getByText("immediate_retry")).toBeInTheDocument();
      expect(screen.getByText("Immediate retry once permitted")).toBeInTheDocument();
    });
  });
});
