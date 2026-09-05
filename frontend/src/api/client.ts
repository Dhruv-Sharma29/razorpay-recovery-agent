/**
 * API client for the Reflow recovery dashboard.
 *
 * This client only calls the backend API. It does NOT:
 * - implement policy logic
 * - call Razorpay directly
 * - call NIM / Nemotron directly
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
  AbResult,
  BatchCaseFrame,
  BatchSummary,
  LearnedOutcomes,
  DashboardResult,
  PaymentEventPayload,
  ProviderStatus,
  ResetResponse,
  RiskSummary,
  ScheduledJobsResponse,
  SchedulerSummary,
} from "../types/dashboard";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

/**
 * Sent as X-API-Key when the backend is configured to require one.
 *
 * This is NOT a secret. Vite inlines env vars into the bundle at build time,
 * so anyone who opens the deployed page can read it. It deters casual traffic
 * against a public demo host; it is not access control. A backend that needs
 * real authentication has to do it per-user on the server.
 */
/** Auth header, or nothing when the backend runs unauthenticated locally. */
function authHeaders(): Record<string, string> {
  const key = import.meta.env.VITE_API_KEY ?? "";
  return key ? { "X-API-Key": key } : {};
}
/** Fine for a single event; a batch is a long-running operation. */
const REQUEST_TIMEOUT_MS = 15000;
/** A batch processes up to 500 events server-side, so it needs real headroom. */
const BATCH_TIMEOUT_MS = 600000;

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

async function requestJson<T>(
  url: string,
  init?: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { ...authHeaders(), ...(init?.headers ?? {}) },
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        timeoutMs >= BATCH_TIMEOUT_MS
          ? "The batch took too long and was cancelled. Try a smaller count."
          : "Request timed out. Is the backend running?",
      );
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

/**
 * Run a fresh batch of synthetic failures end to end.
 *
 * Each call generates new event ids server-side, so repeated runs
 * accumulate instead of colliding on idempotency.
 */
export async function runBatch(
  count: number,
  options: { runScheduler?: boolean; seed?: number; explain?: boolean } = {},
): Promise<BatchSummary> {
  const params = new URLSearchParams({ count: String(count) });
  if (options.runScheduler !== undefined) {
    params.set("run_scheduler", String(options.runScheduler));
  }
  if (options.seed !== undefined) params.set("seed", String(options.seed));
  if (options.explain !== undefined) {
    params.set("explain", String(options.explain));
  }

  return requestJson<BatchSummary>(
    `${API_BASE}/api/dashboard/run-batch?${params}`,
    { method: "POST" },
    BATCH_TIMEOUT_MS,
  );
}

/**
 * Run a batch as a server-sent stream, reporting each case as it lands.
 *
 * Same work and same summary as {@link runBatch}. The difference is when the
 * caller learns about it: with live reasoning on, the server pays one model
 * round trip per event, and this turns that wait into visible progress.
 *
 * Uses fetch rather than EventSource because EventSource cannot carry a
 * timeout and cannot be aborted, and a batch is long enough to need both.
 */
export async function streamBatch(
  count: number,
  options: { runScheduler?: boolean; seed?: number; explain?: boolean } = {},
  handlers: { onCase?: (frame: BatchCaseFrame) => void } = {},
  signal?: AbortSignal,
): Promise<BatchSummary> {
  const params = new URLSearchParams({ count: String(count) });
  if (options.runScheduler !== undefined) {
    params.set("run_scheduler", String(options.runScheduler));
  }
  if (options.seed !== undefined) params.set("seed", String(options.seed));
  if (options.explain !== undefined) {
    params.set("explain", String(options.explain));
  }

  const response = await fetch(
    `${API_BASE}/api/dashboard/run-batch/stream?${params}`,
    { headers: authHeaders(), signal },
  );
  if (!response.ok) {
    throw new Error(`Batch stream failed with status ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Batch stream returned no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let summary: BatchSummary | null = null;
  let streamError: string | null = null;

  const handleFrame = (raw: string) => {
    let name = "message";
    const dataLines: string[] = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return;
    const payload = JSON.parse(dataLines.join("\n"));
    if (name === "case") handlers.onCase?.(payload as BatchCaseFrame);
    else if (name === "summary") summary = payload as BatchSummary;
    else if (name === "error") streamError = payload?.message ?? "Batch failed";
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Frames are separated by a blank line; the tail may be a partial frame.
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      handleFrame(buffer.slice(0, split));
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf("\n\n");
    }
  }
  if (buffer.trim()) handleFrame(buffer);

  if (streamError) throw new Error(streamError);
  if (!summary) {
    throw new Error("Batch stream ended before reporting a summary");
  }
  return summary;
}

/** What the agent has measured about its own recovery actions. */
export async function fetchLearned(): Promise<LearnedOutcomes> {
  return requestJson<LearnedOutcomes>(`${API_BASE}/api/dashboard/learned`);
}

/**
 * Run the same batch twice, differing only in whether the advisor may choose
 * the action, and report what that choice was worth.
 *
 * Two full batches server-side, so it needs the batch timeout rather than
 * the default one.
 */
export async function runAb(count: number, seed?: number): Promise<AbResult> {
  const params = new URLSearchParams({ count: String(count) });
  if (seed !== undefined) params.set("seed", String(seed));
  return requestJson<AbResult>(
    `${API_BASE}/api/dashboard/run-ab?${params}`,
    { method: "POST" },
    BATCH_TIMEOUT_MS,
  );
}

/** Execute deferred retries whose cooldown has elapsed. */
export async function runScheduled(now?: string): Promise<SchedulerSummary> {
  const params = now ? `?now=${encodeURIComponent(now)}` : "";
  return requestJson<SchedulerSummary>(
    `${API_BASE}/api/dashboard/run-scheduled${params}`,
    { method: "POST" },
  );
}

/** Read-only view of scheduled retry jobs. */
export async function getScheduledJobs(
  status?: string,
): Promise<ScheduledJobsResponse> {
  const params = status ? `?status=${encodeURIComponent(status)}` : "";
  return requestJson<ScheduledJobsResponse>(
    `${API_BASE}/api/dashboard/scheduled${params}`,
  );
}

/**
 * Clear recovery state for a clean demo run.
 *
 * Audit history is append-only and is deliberately preserved.
 */
export async function resetState(): Promise<ResetResponse> {
  return requestJson<ResetResponse>(`${API_BASE}/api/dashboard/reset`, {
    method: "POST",
  });
}

/** Revenue-at-risk rollups. Read-only aggregation over the audit log. */
export async function getRisk(limit?: number): Promise<RiskSummary> {
  const params = limit ? `?limit=${limit}` : "";
  return requestJson<RiskSummary>(`${API_BASE}/api/dashboard/risk${params}`);
}

/** Which reasoning provider is configured. Never returns the API key. */
export async function getProvider(): Promise<ProviderStatus> {
  return requestJson<ProviderStatus>(`${API_BASE}/api/dashboard/provider`);
}

/** 
 * Runs a fixed synthetic event through the pipeline to demonstrate a 
 * complete recovery. No payload required. 
 */
export async function runGoldenPath(): Promise<DashboardResult> {
  return requestJson<DashboardResult>(
    `${API_BASE}/api/dashboard/golden-path`,
    { method: "POST" }
  );
}
