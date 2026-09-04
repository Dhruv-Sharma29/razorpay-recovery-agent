/**
 * Manual entry tests.
 *
 * The form only builds an event — it must convert rupees to paise on the
 * wire, flag out-of-range input rather than silently correcting it, and
 * never imply it can authorize anything.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ManualEntry from "../components/ManualEntry";

function setup(disabled = false) {
  const onSubmit = vi.fn();
  render(<ManualEntry disabled={disabled} onSubmit={onSubmit} />);
  return onSubmit;
}

describe("ManualEntry", () => {
  it("submits rupees converted to paise", () => {
    const onSubmit = setup();
    fireEvent.change(screen.getByTestId("manual-amount"), {
      target: { value: "1499" },
    });
    fireEvent.click(screen.getByTestId("manual-submit"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].amount).toBe(149900);
  });

  it("passes the chosen error code and method through", () => {
    const onSubmit = setup();
    fireEvent.change(screen.getByTestId("manual-error-code"), {
      target: { value: "GATEWAY_ERROR" },
    });
    fireEvent.change(screen.getByTestId("manual-method"), {
      target: { value: "card" },
    });
    fireEvent.click(screen.getByTestId("manual-submit"));

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.error_code).toBe("GATEWAY_ERROR");
    expect(payload.payment_method).toBe("card");
  });

  it("flags an out-of-range amount instead of silently fixing it", () => {
    setup();
    const amount = screen.getByTestId("manual-amount");
    fireEvent.change(amount, { target: { value: "500000" } });

    expect(amount).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/Must be between ₹1 and ₹1,00,000/)).toBeInTheDocument();
  });

  it("clamps an out-of-range amount on blur", () => {
    const onSubmit = setup();
    const amount = screen.getByTestId("manual-amount");
    fireEvent.change(amount, { target: { value: "500000" } });
    fireEvent.blur(amount);

    expect(amount).toHaveValue(100000);
    fireEvent.click(screen.getByTestId("manual-submit"));
    expect(onSubmit.mock.calls[0][0].amount).toBe(100000 * 100);
  });

  it("clamps the attempt number to the allowed range", () => {
    const onSubmit = setup();
    fireEvent.change(screen.getByTestId("manual-attempt"), {
      target: { value: "99" },
    });
    fireEvent.click(screen.getByTestId("manual-submit"));
    expect(onSubmit.mock.calls[0][0].attempt_number).toBe(10);
  });

  it("only allows a mandate status on subscriptions", () => {
    setup();
    expect(screen.getByTestId("manual-mandate")).toBeDisabled();

    fireEvent.change(screen.getByTestId("manual-type"), {
      target: { value: "subscription" },
    });
    expect(screen.getByTestId("manual-mandate")).not.toBeDisabled();
  });

  it("sends the mandate status for a subscription failure", () => {
    const onSubmit = setup();
    fireEvent.change(screen.getByTestId("manual-type"), {
      target: { value: "subscription" },
    });
    fireEvent.change(screen.getByTestId("manual-mandate"), {
      target: { value: "expired" },
    });
    fireEvent.click(screen.getByTestId("manual-submit"));

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.type).toBe("subscription");
    expect(payload.mandate_status).toBe("expired");
  });

  it("sends a null mandate for a one-time payment", () => {
    const onSubmit = setup();
    fireEvent.click(screen.getByTestId("manual-submit"));
    expect(onSubmit.mock.calls[0][0].mandate_status).toBeNull();
  });

  it("does not trust the event's self-reported category", () => {
    const onSubmit = setup();
    fireEvent.change(screen.getByTestId("manual-error-code"), {
      target: { value: "INSUFFICIENT_FUNDS" },
    });
    fireEvent.click(screen.getByTestId("manual-submit"));
    // The backend classifies from error_code; the label is never an input.
    expect(onSubmit.mock.calls[0][0].failure_category).toBe("unknown");
  });

  it("generates a fresh event id per submission", () => {
    const onSubmit = setup();
    fireEvent.click(screen.getByTestId("manual-submit"));
    fireEvent.click(screen.getByTestId("manual-submit"));
    const [first] = onSubmit.mock.calls[0];
    const [second] = onSubmit.mock.calls[1];
    expect(first.event_id).not.toBe(second.event_id);
  });

  it("locks the form while a request is in flight", () => {
    setup(true);
    expect(screen.getByTestId("manual-submit")).toBeDisabled();
    expect(screen.getByTestId("manual-amount")).toBeDisabled();
    expect(screen.getByTestId("manual-error-code")).toBeDisabled();
  });
});
