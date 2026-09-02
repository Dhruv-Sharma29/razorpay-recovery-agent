/**
 * API client for the PayPulse recovery dashboard.
 *
 * This client only calls the backend API. It does NOT:
 * - implement policy logic
 * - call Razorpay directly
 * - call Ollama/Qwen directly
 * - authorize recovery independently
 *
 * Hardening notes:
 * - Requests time out instead of hanging forever if the backend is
 *   unreachable or stalls.
 * - FastAPI/Pydantic validation errors (422 responses) are parsed into
 *   a readable "field: message" string instead of raw JSON.
 * - Non-JSON or malformed responses are caught rather than throwing an
 *   unhandled exception inside the UI.
 */

import type {
  AuditLogResponse,
  DashboardResult,
  PaymentEventPayload,
} from "../types/dashboard";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const REQUEST_TIMEOUT_MS = 15000;

interface FastApiValidationError {
  loc?: (string | number)[];
  msg?: string;
}

interface FastApiErrorBody {
  detail?: string | FastApiValidationError[];
}

async function parseErrorDetail(response: Response): Promise<string> {
  let text: string;
  try {
    text = await response.text();
  } catch {
    return `Request failed with status ${response.status}`;
  }

  if (!text) {
    return `Request failed with status ${response.status}`;
  }

  try {
    const body = JSON.parse(text) as FastApiErrorBody;
    if (typeof body.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body.detail)) {
      const messages = body.detail.map((entry) => {
        const field = Array.isArray(entry.loc)
          ? entry.loc.filter((part) => part !== "body").join(".")
          : "value";
        return `${field || "value"}: ${entry.msg ?? "is invalid"}`;
      });
      if (messages.length > 0) return messages.join("; ");
    }
  } catch {
    // Response wasn't JSON — fall back to the raw text below.
  }

  return text;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("Request timed out. Is the backend running?");
    }
    throw new Error(
      "Could not reach the backend. Check that it is running and reachable.",
    );
  } finally {
    clearTimeout(timeoutId);
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    throw new Error(`Backend error (${response.status}): ${detail}`);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new Error("Received an invalid response from the backend.");
  }
}

/**
 * Process a payment event through the recovery pipeline.
 * All decisions are made by the backend.
 */
export async function processPayment(
  event: PaymentEventPayload,
): Promise<DashboardResult> {
  return requestJson<DashboardResult>(`${API_BASE}/api/dashboard/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(event),
  });
}

/**
 * Retrieve the audit log from the backend. Read-only.
 */
export async function getAuditLog(): Promise<AuditLogResponse> {
  return requestJson<AuditLogResponse>(`${API_BASE}/api/dashboard/audit`);
}
