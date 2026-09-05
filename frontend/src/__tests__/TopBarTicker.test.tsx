/**
 * The recovery ticker.
 *
 * It is the one number visible on every view, so the rules about when it may
 * appear matter more than how it looks: never a placeholder zero, and never a
 * rate taken from a denominator the rest of the console avoids.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import TopBar from "../layout/TopBar";

const BASE = {
  providerModel: "nvidia/nemotron-3-nano",
  providerConfigured: true,
  lastWasFallback: null,
  running: false,
  onRun: () => {},
  onReset: () => {},
  totalRecovered: null as number | null,
  recoveryRate: null as number | null,
};

describe("TopBar recovery ticker", () => {
  it("stays hidden until a real figure exists", () => {
    render(<TopBar {...BASE} />);
    expect(screen.queryByTestId("recovery-ticker")).not.toBeInTheDocument();
  });

  it("shows the recovered amount once known", () => {
    render(<TopBar {...BASE} totalRecovered={1436200} />);
    expect(screen.getByTestId("ticker-amount")).toBeInTheDocument();
  });

  it("shows a genuine zero rather than hiding it", () => {
    // Nothing recovered yet is a fact worth stating; only *unknown* hides.
    render(<TopBar {...BASE} totalRecovered={0} />);
    expect(screen.getByTestId("recovery-ticker")).toBeInTheDocument();
  });

  it("omits the rate until a batch has produced one", () => {
    render(<TopBar {...BASE} totalRecovered={1436200} />);
    expect(screen.queryByTestId("ticker-rate")).not.toBeInTheDocument();
  });

  it("labels the rate by its denominator", () => {
    render(<TopBar {...BASE} totalRecovered={1436200} recoveryRate={0.67} />);
    expect(screen.getByTestId("ticker-rate")).toHaveTextContent(
      /of recoverable/i,
    );
  });

  it("announces changes politely rather than interrupting", () => {
    render(<TopBar {...BASE} totalRecovered={1436200} />);
    const ticker = screen.getByTestId("recovery-ticker");
    expect(ticker).toHaveAttribute("aria-live", "polite");
    expect(ticker).toHaveAttribute("role", "status");
  });

  it("does not disturb the AI status pill", () => {
    render(<TopBar {...BASE} totalRecovered={1436200} />);
    expect(screen.getByTestId("ai-status-pill")).toBeInTheDocument();
  });
});
