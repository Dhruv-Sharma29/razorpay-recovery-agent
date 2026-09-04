/** Sidebar navigation tests. */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Sidebar from "../layout/Sidebar";

describe("Sidebar", () => {
  it("shows the product brand and buildathon track", () => {
    render(<Sidebar active="overview" onNavigate={() => {}} />);
    expect(screen.getByText("Reflow")).toBeInTheDocument();
    expect(screen.getByText("AI Revenue Recovery")).toBeInTheDocument();
    expect(screen.getByText(/Track 03/)).toBeInTheDocument();
  });

  it("marks the active view for assistive tech", () => {
    render(<Sidebar active="cases" onNavigate={() => {}} />);
    expect(screen.getByTestId("nav-cases")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByTestId("nav-overview")).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("navigates on click", () => {
    const onNavigate = vi.fn();
    render(<Sidebar active="overview" onNavigate={onNavigate} />);
    fireEvent.click(screen.getByTestId("nav-agent"));
    expect(onNavigate).toHaveBeenCalledWith("agent");
  });
});
