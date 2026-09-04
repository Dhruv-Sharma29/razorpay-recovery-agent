/**
 * AI contribution tests.
 *
 * Every figure must come from the batch response, and the section must not
 * imply the model can change an outcome — it structurally cannot.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AiContribution from "../components/AiContribution";
import type { BatchSummary } from "../types/dashboard";

function summary(reasoning: Partial<BatchSummary["reasoning"]> | null): BatchSummary {
  return {
    transactions_processed: 14,
    total_attempted_amount: 1_000_000,
    total_recovered_amount: 250_000,
    recovery_rate_by_amount: 0.25,
    recovery_rate_by_count: 0.2,
    outcomes: { recovered: 3 },
    funnel: { raw: 14, needed_signal: 14, contacted: 3, confirmed_recovered: 3 },
    by_scenario: [],
    audit_ids: [],
    scheduler: null,
    simulated: true,
    duration_seconds: 0.3,
    reasoning: reasoning
      ? {
          mode: "model",
          consultations: 14,
          model_generated: 14,
          fallback: 0,
          from_cache: 0,
          customer_messages: 14,
          overrode_policy: 0,
          model: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
          ...reasoning,
        }
      : undefined,
  } as BatchSummary;
}

function card() {
  return within(screen.getByTestId("ai-contribution"));
}

describe("AiContribution", () => {
  it("renders nothing before a batch has run", () => {
    const { container } = render(<AiContribution summary={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the batch reported no reasoning block", () => {
    const { container } = render(<AiContribution summary={summary(null)} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the consultation count from the batch", () => {
    render(<AiContribution summary={summary({})} />);
    const tile = card().getByText("Consultations").closest(".ai-metric");
    expect(within(tile as HTMLElement).getByText("14")).toBeInTheDocument();
  });

  it("shows real model answers as a ratio with the fallback count", () => {
    render(<AiContribution summary={summary({ model_generated: 11, fallback: 3, from_cache: 0 })} />);
    expect(card().getByText("11 of 14")).toBeInTheDocument();
    expect(card().getByText("0 cached, 3 fell back safely")).toBeInTheDocument();
  });

  it("reports zero overrides and says why they cannot happen", () => {
    render(<AiContribution summary={summary({})} />);
    const tile = card().getByText("Policy overrides — must be 0").closest(".ai-metric");
    expect(within(tile as HTMLElement).getByText("0")).toBeInTheDocument();
    expect(
      within(tile as HTMLElement).getByText(/cannot override a policy decision/i),
    ).toBeInTheDocument();
  });

  it("shows how many customer messages survived the compliance filter", () => {
    render(<AiContribution summary={summary({ customer_messages: 9 })} />);
    const tile = card()
      .getByText("Customer messages drafted")
      .closest(".ai-metric");
    expect(within(tile as HTMLElement).getByText("9")).toBeInTheDocument();
  });

  it("reads every figure from the batch rather than hardcoding", () => {
    render(
      <AiContribution
        summary={summary({
          consultations: 40,
          model_generated: 37,
          fallback: 3,
          from_cache: 0,
          customer_messages: 21,
        })}
      />,
    );
    expect(card().getByText("40")).toBeInTheDocument();
    expect(card().getByText("37 of 40")).toBeInTheDocument();
    expect(card().getByText("21")).toBeInTheDocument();
  });

  it("explains that a skipped batch used the fallback path", () => {
    render(<AiContribution summary={summary({ mode: "skipped", model_generated: 0, fallback: 14 })} />);
    expect(
      card().getByText(/ran without live reasoning/i),
    ).toBeInTheDocument();
  });

  it("states no override happened, and that none can", () => {
    render(<AiContribution summary={summary({})} />);
    const overrides = within(screen.getByTestId("ai-overrides"));
    expect(overrides.getByText(/No consultation in this run overrode/i)).toBeInTheDocument();
    expect(overrides.getByText(/copies the policy engine's verdict verbatim/i)).toBeInTheDocument();
    expect(overrides.getByText(/See the Cases page/i)).toBeInTheDocument();
  });

  it("treats a non-zero override as a safety failure, not a feature", () => {
    render(<AiContribution summary={summary({ overrode_policy: 2 })} />);
    const overrides = within(screen.getByTestId("ai-overrides"));
    expect(overrides.getByText(/should be impossible/i)).toBeInTheDocument();
    expect(overrides.getByText(/safety-boundary failure/i)).toBeInTheDocument();
  });
});
