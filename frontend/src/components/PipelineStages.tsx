/**
 * PipelineStages component.
 *
 * Renders the recovery pipeline as a connected horizontal rail of seven
 * nodes rather than six independent cards, so the sequence money moves
 * through is visible as a sequence.
 *
 * Numbered markers are deliberate here: the content genuinely is an
 * ordered pipeline, not an arbitrary card grid.
 *
 * This component only renders backend data — no policy logic.
 */

import type { DashboardResult } from "../types/dashboard";
import { formatInr } from "../utils/currency";

interface PipelineStagesProps {
  result: DashboardResult;
}

interface Stage {
  key: string;
  label: string;
  value: string;
  detail: string;
  /** Secondary line, kept as its own node so each backend field stays
      independently addressable rather than concatenated into prose. */
  meta?: string;
  /**
   * How far the model's authority extends at this stage.
   *
   * "bounded" — it may pick among the actions policy already authorised,
   * and can neither add one nor authorise recovery itself.
   * "advisory" — it cannot affect the outcome at all; the stage runs after
   * the decision is made and only produces text.
   *
   * Absent means the stage is deterministic and decides for itself.
   */
  authority?: "bounded" | "advisory";
  /** Render value/detail in monospace (IDs, amounts, timestamps). */
  mono?: boolean;
  reached: boolean;
}

function formatAmount(paise: number | null): string {
  if (paise === null || paise === undefined) return "—";
  return formatInr(paise);
}

function buildStages(result: DashboardResult): Stage[] {
  return [
    {
      key: "recommendation",
      label: "AI Recommendation",
      value: result.ai_suggested_action ?? "No recommendation",
      detail:
        result.recommendation_reason ??
        (result.revenue_at_risk === true
          ? "Revenue-risk signal detected"
          : "No AI recommendation available"),
      meta:
        result.recommendation_status
          ? `Policy treatment: ${result.recommendation_status}`
          : result.risk_score !== null && result.risk_score !== undefined
            ? `Risk score: ${Math.round(result.risk_score * 100)}%`
            : undefined,
      authority: "bounded",
      reached:
        result.recommendation_success !== null &&
        result.recommendation_success !== undefined,
    },
    {
      key: "classification",
      label: "Classification",
      value: result.failure_category ?? "Not classified",
      detail: result.classification_reason ?? "No classification data available",
      reached: result.failure_category !== null,
    },
    {
      key: "policy",
      label: "Policy Decision",
      value: result.policy_action ?? "No decision",
      detail: result.policy_reason ?? "No policy data available",
      meta:
        result.automatic_recovery_allowed !== null
          ? `Recovery allowed: ${result.automatic_recovery_allowed ? "Yes" : "No"}`
          : undefined,
      reached: result.policy_action !== null,
    },
    {
      key: "reasoning",
      label: "Reasoning",
      value: result.reasoning_recommendation ?? "No recommendation",
      detail: result.reasoning_explanation ?? "No reasoning data available",
      meta:
        result.reasoning_success !== null
          ? `Reasoning succeeded: ${result.reasoning_success ? "Yes" : "No"}`
          : undefined,
      authority: "advisory",
      reached: result.reasoning_recommendation !== null,
    },
    {
      key: "execution",
      label: "Execution",
      value: result.execution_status ?? "Not executed",
      detail: result.execution_reason ?? "Execution was not attempted",
      reached: result.execution_status !== null,
    },
    {
      key: "escalation",
      label: "Escalation",
      value: result.escalation_status ?? "Not evaluated",
      detail: result.escalation_reason ?? "No escalation data available",
      meta: result.escalation_severity
        ? `Severity: ${result.escalation_severity}`
        : undefined,
      reached: result.escalation_status !== null,
    },
    {
      key: "transaction",
      label: "Transaction Info",
      value: formatAmount(result.amount),
      detail: `Attempt: ${result.attempt_number ?? "—"}`,
      meta: result.timestamp,
      mono: true,
      reached: result.amount !== null,
    },
  ];
}

export default function PipelineStages({ result }: PipelineStagesProps) {
  const stages = buildStages(result);

  return (
    <div className="pipeline-rail" data-testid="pipeline-stages">
      {stages.map((stage, i) => (
        <div
          key={stage.key}
          className={`rail-node ${stage.reached ? "rail-node--active" : "rail-node--pending"}`}
        >
          {i < stages.length - 1 && (
            <div
              className={`rail-connector ${
                stages[i + 1].reached ? "rail-connector--active" : ""
              }`}
              aria-hidden="true"
            />
          )}
          <div className="rail-node__marker">{i + 1}</div>
          <div className="rail-node__label">
            {stage.label}
            {stage.authority && (
              <span
                className="rail-node__advisory"
                data-authority={stage.authority}
                title={
                  stage.authority === "bounded"
                    ? "May choose among the actions policy permits. Cannot add an action, raise a limit, or authorise recovery."
                    : "Produces explanation text only. Runs after the decision and cannot change it."
                }
              >
                {stage.authority === "bounded" ? "Bounded" : "Advisory only"}
              </span>
            )}
          </div>
          {stage.reached && (
            <div className="rail-node__detail">
              <div
                className={`rail-node__value${stage.mono ? " data-mono" : ""}`}
              >
                {stage.value}
              </div>
              <div
                className={`rail-node__reason${stage.mono ? " data-mono" : ""}`}
              >
                {stage.detail}
              </div>
              {stage.meta && (
                <div
                  className={`rail-node__reason${stage.mono ? " data-mono" : ""}`}
                >
                  {stage.meta}
                </div>
              )}
            </div>
          )}
        </div>
      ))}

      {result.error && (
        <div className="rail-error" role="alert">
          Error: {result.error}
        </div>
      )}
    </div>
  );
}
