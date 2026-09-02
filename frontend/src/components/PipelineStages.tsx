/**
 * PipelineStages component (TASK-013).
 *
 * Premium Fintech Redesign.
 * Displays each stage of the recovery pipeline as a beautiful connected journey.
 * This component only renders backend data — no policy logic.
 */

import type { DashboardResult } from "../types/dashboard";

interface PipelineStagesProps {
  result: DashboardResult;
}

function getIconStatus(stage: string, result: DashboardResult): "active" | "success" | "warning" | "danger" | "default" {
  switch (stage) {
    case "classification":
      return result.failure_category && result.failure_category !== "unknown" ? "success" : "warning";
    case "policy":
      return result.automatic_recovery_allowed === true ? "success" : "warning";
    case "reasoning":
      return result.reasoning_success ? "success" : "warning";
    case "execution":
      return result.execution_status === "success" ? "success" :
             result.execution_status === "failed" ? "danger" : "default";
    case "escalation":
      return result.escalation_status && result.escalation_status !== "not_required" ? "warning" : "default";
    case "audit":
      return "success";
    default:
      return "default";
  }
}

export default function PipelineStages({ result }: PipelineStagesProps) {
  return (
    <div className="pipeline-journey" data-testid="pipeline-stages">
      {/* 01 Classification */}
      <div className="pipeline-node">
        <div className={`pipeline-icon ${getIconStatus("classification", result)}`}>01</div>
        <div className="pipeline-content">
          <h4>Classification</h4>
          <p>{result.failure_category ?? "Not classified"}</p>
        </div>
      </div>

      {/* 02 Policy */}
      <div className="pipeline-node">
        <div className={`pipeline-icon ${getIconStatus("policy", result)}`}>02</div>
        <div className="pipeline-content">
          <h4>Policy</h4>
          <p>{result.policy_action ? result.policy_action.replace(/_/g, " ") : "No decision"}</p>
        </div>
      </div>

      {/* 03 Reasoning */}
      <div className="pipeline-node">
        <div className={`pipeline-icon ${getIconStatus("reasoning", result)}`}>03</div>
        <div className="pipeline-content">
          <h4>Reasoning</h4>
          <p>{result.reasoning_success ? "Generated" : "Failed / Skipped"}</p>
        </div>
      </div>

      {/* 04 Execution */}
      <div className="pipeline-node">
        <div className={`pipeline-icon ${getIconStatus("execution", result)}`}>04</div>
        <div className="pipeline-content">
          <h4>Execution</h4>
          <p>{result.execution_status ? result.execution_status.replace(/_/g, " ") : "Not attempted"}</p>
        </div>
      </div>

      {/* 05 Escalation */}
      <div className="pipeline-node">
        <div className={`pipeline-icon ${getIconStatus("escalation", result)}`}>05</div>
        <div className="pipeline-content">
          <h4>Escalation</h4>
          <p>{result.escalation_status ? result.escalation_status.replace(/_/g, " ") : "Not evaluated"}</p>
        </div>
      </div>

      {/* 06 Audit */}
      <div className="pipeline-node">
        <div className={`pipeline-icon ${getIconStatus("audit", result)}`}>06</div>
        <div className="pipeline-content">
          <h4>Audit</h4>
          <p>Recorded successfully</p>
        </div>
      </div>
    </div>
  );
}
