/**
 * API client for the Recovery Agent dashboard (TASK-009).
 *
 * This client only calls the backend API. It does NOT:
 * - implement policy logic
 * - call Razorpay directly
 * - call Ollama/Qwen directly
 * - authorize recovery independently
 */

import type {
  AuditLogResponse,
  DashboardResult,
  PaymentEventPayload,
} from "../types/dashboard";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

/**
 * Process a payment event through the recovery pipeline.
 * All decisions are made by the backend.
 */
export async function processPayment(
  event: PaymentEventPayload,
): Promise<DashboardResult> {
  const response = await fetch(`${API_BASE}/api/dashboard/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(event),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Backend error (${response.status}): ${detail}`);
  }

  return response.json() as Promise<DashboardResult>;
}

/**
 * Retrieve the audit log from the backend. Read-only.
 */
export async function getAuditLog(): Promise<AuditLogResponse> {
  const response = await fetch(`${API_BASE}/api/dashboard/audit`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Backend error (${response.status}): ${detail}`);
  }

  return response.json() as Promise<AuditLogResponse>;
}
