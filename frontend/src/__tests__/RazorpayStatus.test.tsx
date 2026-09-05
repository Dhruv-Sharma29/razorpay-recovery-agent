/**
 * Razorpay integration status.
 *
 * The panel exists so nobody has to take "it's wired up" on trust. The tests
 * that matter are the ones stopping it overclaiming: valid credentials with
 * a simulated executor must not read as a live integration.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import RazorpayStatus from "../components/RazorpayStatus";
import type { RazorpayStatus as Status } from "../types/dashboard";

function status(over: Partial<Status> = {}): Status {
  return {
    status: "ok",
    detail: "Razorpay accepted these credentials. Live calls are enabled.",
    executor_mode: "razorpay_test",
    live_calls_enabled: true,
    key_configured: true,
    key_id: "rzp_test_ABC…56",
    test_mode: true,
    reachable: true,
    payments_visible: 2,
    checked_at: "2026-09-05T00:00:00Z",
    ...over,
  };
}

const noop = () => {};

describe("RazorpayStatus", () => {
  it("reads as live only when calls actually go out", () => {
    render(<RazorpayStatus status={status()} checking={false} onRecheck={noop} />);
    expect(screen.getByTestId("rzp-headline")).toHaveAttribute("data-tone", "ok");
    expect(screen.getByTestId("rzp-headline")).toHaveTextContent(/live/i);
  });

  it("does not claim a live integration while the executor is mocked", () => {
    // The trap this panel exists to close: keys validate, nothing is sent.
    render(
      <RazorpayStatus
        status={status({
          live_calls_enabled: false,
          executor_mode: "mock",
          detail: "EXECUTOR_MODE is 'mock', so recoveries are still simulated.",
        })}
        checking={false}
        onRecheck={noop}
      />,
    );
    const headline = screen.getByTestId("rzp-headline");
    expect(headline).toHaveAttribute("data-tone", "hold");
    expect(headline).toHaveTextContent(/simulated/i);
    expect(headline).not.toHaveTextContent(/^Live/);
  });

  it("marks rejected credentials as a stop, not a warning", () => {
    render(
      <RazorpayStatus
        status={status({ status: "unauthorized", live_calls_enabled: true })}
        checking={false}
        onRecheck={noop}
      />,
    );
    expect(screen.getByTestId("rzp-headline")).toHaveAttribute(
      "data-tone",
      "stop",
    );
  });

  it("marks a refused live key as a stop", () => {
    render(
      <RazorpayStatus
        status={status({ status: "live_key_refused" })}
        checking={false}
        onRecheck={noop}
      />,
    );
    expect(screen.getByTestId("rzp-headline")).toHaveTextContent(
      /live key refused/i,
    );
  });

  it("treats missing configuration as a warning rather than a failure", () => {
    render(
      <RazorpayStatus
        status={status({ status: "not_configured", key_configured: false })}
        checking={false}
        onRecheck={noop}
      />,
    );
    expect(screen.getByTestId("rzp-headline")).toHaveAttribute(
      "data-tone",
      "hold",
    );
  });

  it("passes the backend's own guidance through verbatim", () => {
    render(
      <RazorpayStatus
        status={status({ detail: "Add them to backend/.env" })}
        checking={false}
        onRecheck={noop}
      />,
    );
    expect(screen.getByTestId("rzp-detail")).toHaveTextContent(
      "Add them to backend/.env",
    );
  });

  it("shows the masked key, never a full one", () => {
    render(<RazorpayStatus status={status()} checking={false} onRecheck={noop} />);
    expect(screen.getByTestId("rzp-key")).toHaveTextContent("rzp_test_ABC…56");
  });

  it("says the status is unknown rather than implying a verdict", () => {
    render(<RazorpayStatus status={null} checking={false} onRecheck={noop} />);
    expect(screen.getByTestId("rzp-unknown")).toHaveTextContent(
      /unavailable/i,
    );
    expect(screen.queryByTestId("rzp-headline")).not.toBeInTheDocument();
  });

  it("can be re-checked on demand", () => {
    const onRecheck = vi.fn();
    render(
      <RazorpayStatus status={status()} checking={false} onRecheck={onRecheck} />,
    );
    fireEvent.click(screen.getByTestId("rzp-recheck"));
    expect(onRecheck).toHaveBeenCalledTimes(1);
  });

  it("disables the control while a check is in flight", () => {
    render(<RazorpayStatus status={null} checking onRecheck={noop} />);
    expect(screen.getByTestId("rzp-recheck")).toBeDisabled();
    expect(screen.getByTestId("rzp-unknown")).toHaveTextContent(/checking/i);
  });
});
