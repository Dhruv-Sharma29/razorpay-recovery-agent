/**
 * Theme toggle tests.
 *
 * The theme must survive a reload and must not crash when storage is
 * unavailable (private browsing), where localStorage throws on access.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ThemeToggle from "../components/ThemeToggle";

function toggle() {
  return screen.getByTestId("theme-toggle");
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ThemeToggle", () => {
  it("defaults to light and applies it to the document", () => {
    render(<ThemeToggle />);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(toggle()).toHaveTextContent("Dark mode");
  });

  it("toggles the document theme and persists the choice", () => {
    render(<ThemeToggle />);
    fireEvent.click(toggle());

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
    expect(toggle()).toHaveTextContent("Light mode");
  });

  it("restores a stored theme on mount", () => {
    localStorage.setItem("theme", "dark");
    render(<ThemeToggle />);
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("adopts the theme already painted by the inline script", () => {
    // index.html resolves the theme before first paint; React must agree
    // with it rather than flipping back.
    document.documentElement.dataset.theme = "dark";
    render(<ThemeToggle />);
    expect(toggle()).toHaveTextContent("Light mode");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("falls back to the OS preference when nothing is stored", () => {
    // jsdom does not implement matchMedia, so define it rather than spy.
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: (q: string) => ({ matches: q.includes("dark") }) as MediaQueryList,
    });
    render(<ThemeToggle />);
    expect(document.documentElement.dataset.theme).toBe("dark");
    delete (window as { matchMedia?: unknown }).matchMedia;
  });

  it("still works when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    expect(() => render(<ThemeToggle />)).not.toThrow();
    fireEvent.click(toggle());
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

});
