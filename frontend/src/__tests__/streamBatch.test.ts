/**
 * The SSE reader has to reassemble frames from arbitrary chunk boundaries —
 * the network decides where the splits fall, not the server — so these tests
 * deliberately cut frames in half.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { streamBatch } from "../api/client";
import type { BatchCaseFrame } from "../types/dashboard";

const CASE = {
  index: 1,
  total: 2,
  payment_id: "pay_one",
  amount: 1000,
  category: "insufficient_funds",
  action: "scheduled_retry",
  allowed: true,
  escalation_reason: null,
  recovered: false,
  outcome: "recovery_scheduled",
};

const SUMMARY = { transactions_processed: 2, total_recovered_amount: 500 };

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** Serve a body in exactly the chunks given, byte boundaries included. */
function mockStream(chunks: string[], ok = true, status = 200) {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok, status, body } as unknown as Response),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("API authentication", () => {
  it("sends X-API-Key when the deployed backend requires one", async () => {
    vi.stubEnv("VITE_API_KEY", "demo-key-123");
    mockStream([sse("summary", SUMMARY)]);
    await streamBatch(1);
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe(
      "demo-key-123",
    );
  });

  it("sends no auth header when running unauthenticated locally", async () => {
    vi.stubEnv("VITE_API_KEY", "");
    mockStream([sse("summary", SUMMARY)]);
    await streamBatch(1);
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBeUndefined();
  });
});

describe("streamBatch", () => {
  it("reports each case and returns the final summary", async () => {
    mockStream([
      sse("start", { count: 2 }),
      sse("case", CASE),
      sse("case", { ...CASE, index: 2, payment_id: "pay_two" }),
      sse("summary", SUMMARY),
    ]);
    const seen: BatchCaseFrame[] = [];
    const summary = await streamBatch(2, {}, { onCase: (f) => seen.push(f) });
    expect(seen.map((f) => f.payment_id)).toEqual(["pay_one", "pay_two"]);
    expect(summary.transactions_processed).toBe(2);
  });

  it("reassembles a frame split across chunks", async () => {
    const whole = sse("case", CASE) + sse("summary", SUMMARY);
    // Split mid-JSON, which is where a naive per-chunk parser breaks.
    const cut = Math.floor(whole.length / 3);
    mockStream([whole.slice(0, cut), whole.slice(cut)]);
    const seen: BatchCaseFrame[] = [];
    const summary = await streamBatch(1, {}, { onCase: (f) => seen.push(f) });
    expect(seen).toHaveLength(1);
    expect(seen[0].payment_id).toBe("pay_one");
    expect(summary.transactions_processed).toBe(2);
  });

  it("handles several frames arriving in one chunk", async () => {
    mockStream([sse("case", CASE) + sse("case", { ...CASE, index: 2 }) + sse("summary", SUMMARY)]);
    const seen: BatchCaseFrame[] = [];
    await streamBatch(2, {}, { onCase: (f) => seen.push(f) });
    expect(seen).toHaveLength(2);
  });

  it("throws the server's message when the run fails mid-stream", async () => {
    mockStream([sse("case", CASE), sse("error", { message: "pipeline exploded" })]);
    await expect(streamBatch(1)).rejects.toThrow("pipeline exploded");
  });

  it("throws when the stream ends without a summary", async () => {
    mockStream([sse("case", CASE)]);
    await expect(streamBatch(1)).rejects.toThrow(/summary/i);
  });

  it("throws on a non-ok response so the caller can fall back", async () => {
    mockStream([], false, 502);
    await expect(streamBatch(1)).rejects.toThrow(/502/);
  });

  it("passes the run options through as query parameters", async () => {
    mockStream([sse("summary", SUMMARY)]);
    await streamBatch(7, { runScheduler: false, seed: 42, explain: true });
    const url = String(vi.mocked(fetch).mock.calls[0][0]);
    expect(url).toContain("count=7");
    expect(url).toContain("run_scheduler=false");
    expect(url).toContain("seed=42");
    expect(url).toContain("explain=true");
  });
});
