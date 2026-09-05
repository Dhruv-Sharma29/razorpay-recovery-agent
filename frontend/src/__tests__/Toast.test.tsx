/**
 * Toast utility.
 *
 * A confirmation that never leaves is clutter, and one that interrupts a
 * screen reader mid-sentence is worse than none — so the tests pin dismissal
 * and politeness rather than appearance.
 */

import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster, useToast } from "../components/Toast";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useToast", () => {
  it("starts empty", () => {
    const { result } = renderHook(() => useToast());
    expect(result.current.toasts).toEqual([]);
  });

  it("queues a message with its tone", () => {
    const { result } = renderHook(() => useToast());
    act(() => result.current.push("Recovered", "ok"));
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe("Recovered");
    expect(result.current.toasts[0].tone).toBe("ok");
  });

  it("defaults to the ok tone", () => {
    const { result } = renderHook(() => useToast());
    act(() => result.current.push("Done"));
    expect(result.current.toasts[0].tone).toBe("ok");
  });

  it("keeps several at once without collision", () => {
    const { result } = renderHook(() => useToast());
    act(() => {
      result.current.push("first");
      result.current.push("second");
    });
    expect(result.current.toasts).toHaveLength(2);
    const ids = result.current.toasts.map((t) => t.id);
    expect(new Set(ids).size).toBe(2);
  });

  it("dismisses itself so it never becomes furniture", () => {
    const { result } = renderHook(() => useToast());
    act(() => result.current.push("temporary"));
    act(() => vi.advanceTimersByTime(4000));
    expect(result.current.toasts).toEqual([]);
  });

  it("dismisses only the one asked for", () => {
    const { result } = renderHook(() => useToast());
    act(() => {
      result.current.push("keep");
      result.current.push("drop");
    });
    const dropId = result.current.toasts[1].id;
    act(() => result.current.dismiss(dropId));
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe("keep");
  });
});

describe("Toaster", () => {
  it("renders nothing when there is nothing to say", () => {
    const { container } = render(<Toaster toasts={[]} onDismiss={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("announces politely rather than interrupting", () => {
    render(
      <Toaster
        toasts={[{ id: 1, message: "Recovered", tone: "ok" }]}
        onDismiss={() => {}}
      />,
    );
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
  });

  it("carries the tone through for the signal border", () => {
    render(
      <Toaster
        toasts={[{ id: 1, message: "Refused", tone: "stop" }]}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByTestId("toast")).toHaveAttribute("data-tone", "stop");
  });

  it("can be dismissed by keyboard and pointer alike", () => {
    const onDismiss = vi.fn();
    render(
      <Toaster
        toasts={[{ id: 7, message: "Recovered", tone: "ok" }]}
        onDismiss={onDismiss}
      />,
    );
    const close = screen.getByTestId("toast-dismiss");
    expect(close).toHaveAccessibleName(/dismiss/i);
    fireEvent.click(close);
    expect(onDismiss).toHaveBeenCalledWith(7);
  });
});
