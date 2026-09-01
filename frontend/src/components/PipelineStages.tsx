/**
 * PipelineStages component (TASK-009).
 *
 * Displays each stage of the recovery pipeline as a visual card grid.
 * This component only renders backend data — no policy logic.
 */

import type { DashboardResult } from "../types/dashboard";

interface PipelineStagesProps {
  result: DashboardResult;
}

function formatAmount(paise: number | null): string {
  if (paise === null || paise === undefined) return "—";
  return `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
}

export default function PipelineStages({ result }: PipelineStagesProps) {
  return (
    <div className="pipeline-stages" data-testid="pipeline-stages">
      {/* 1. Classification */}
      <div className="card stage">
        <div className="stage-header">
          <span className="stage-label">Classification</span>
          <span className="stage-number">1</span>
        </div>
        <div className="stage-value">
          {result.failure_category ?? "Not classified"}
        </div>
        <div className="stage-detail">
          {result.classification_reason ?? "No classification data available"}
        </div>
      </div>

      {/* 2. Policy Decision */}
      <div className="card stage">
        <div className="stage-header">
          <span className="stage-label">Policy Decision</span>
          <span className="stage-number">2</span>
        </div>
        <div className="stage-value">
          {result.policy_action ?? "No decision"}
        </div>
        <div className="stage-detail">
          {result.policy_reason ?? "No policy data available"}
        </div>
        {result.automatic_recovery_allowed !== null && (
          <div className="stage-detail">
            Recovery allowed:{" "}
            <strong>{result.automatic_recovery_allowed ? "Yes" : "No"}</strong>
          </div>
        )}
      </div>

      {/* 3. Reasoning */}
      <div className="card stage">
        <div className="stage-header">
          <span className="stage-label">Reasoning</span>
          <span className="stage-number">3</span>
        </div>
        <div className="stage-value">
          {result.reasoning_recommendation ?? "No recommendation"}
        </div>
        <div className="stage-detail">
          {result.reasoning_explanation ?? "No reasoning data available"}
        </div>
        {result.reasoning_success !== null && (
          <div className="stage-detail">
            Reasoning succeeded:{" "}
            <strong>{result.reasoning_success ? "Yes" : "No"}</strong>
          </div>
        )}
      </div>

      {/* 4. Execution */}
      <div className="card stage">
        <div className="stage-header">
          <span className="stage-label">Execution</span>
          <span className="stage-number">4</span>
        </div>
        <div className="stage-value">
          {result.execution_status ?? "Not executed"}
        </div>
        <div className="stage-detail">
          {result.execution_reason ?? "Execution was not attempted"}
        </div>
      </div>

      {/* 5. Escalation */}
      <div className="card stage">
        <div className="stage-header">
          <span className="stage-label">Escalation</span>
          <span className="stage-number">5</span>
        </div>
        <div className="stage-value">
          {result.escalation_status ?? "Not evaluated"}
        </div>
        <div className="stage-detail">
          {result.escalation_reason ?? "No escalation data available"}
        </div>
        {result.escalation_severity && (
          <div className="stage-detail">
            Severity: <strong>{result.escalation_severity}</strong>
          </div>
        )}
      </div>

      {/* 6. Metadata */}
      <div className="card stage">
        <div className="stage-header">
          <span className="stage-label">Transaction Info</span>
          <span className="stage-number">6</span>
        </div>
        <div className="stage-value">
          {formatAmount(result.amount)}
        </div>
        <div className="stage-detail">
          Attempt: {result.attempt_number ?? "—"}
        </div>
        <div className="stage-detail">
          Timestamp: {result.timestamp}
        </div>
        {result.error && (
          <div className="stage-detail" style={{ color: "var(--status-failed)" }}>
            Error: {result.error}
          </div>
        )}
      </div>
    </div>
  );
}
