/**
 * CategoryChart tests.
 *
 * recharts' ResponsiveContainer measures its parent, which is 0x0 under
 * jsdom, so it would render nothing. We stub it to inject a fixed size so
 * the chart actually draws and its axis labels can be asserted.
 */

import { cloneElement, type ReactElement } from "react";

import { render } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import CategoryChart from "../components/CategoryChart";

vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement<Record<string, unknown>> }) =>
      cloneElement(children, { width: 400, height: 300 }),
  };
});

describe("CategoryChart", () => {
  const data = [
    { category: "insufficient_funds", recoveryRate: 80 },
    { category: "network_error", recoveryRate: 40 },
  ];

  it("renders an SVG chart surface for the provided data", () => {
    const { container } = render(<CategoryChart data={data} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("labels every category on the axis", () => {
    const { container } = render(<CategoryChart data={data} />);
    expect(container.textContent).toContain("insufficient_funds");
    expect(container.textContent).toContain("network_error");
  });
});
