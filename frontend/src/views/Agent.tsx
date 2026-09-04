/**
 * Agent: what the model actually contributed.
 *
 * The model explains; it never decides. Every panel here is advisory
 * output attached to a decision the policy engine already made, and is
 * badged as model-generated or deterministic fallback.
 */

import ManualEntry from "../components/ManualEntry";
import PipelineStages from "../components/PipelineStages";
import StatusBadge from "../components/StatusBadge";
import type { DashboardResult, PaymentEventPayload } from "../types/dashboard";
import { formatRupees } from "../utils/format";

interface SampleCase {
  key: string;
  label: string;
  tone: "primary" | "adversarial";
  isGoldenPath?: boolean;
  build: () => PaymentEventPayload;
}

interface AgentProps {
  result: DashboardResult | null;
  loading: boolean;
  error: string | null;
  cases: SampleCase[];
  onRunSample: (payload: PaymentEventPayload) => void;
  onRunGoldenPath: () => void;
}

export default function Agent({
  result,
  loading,
  error,
  cases,
  onRunSample,
  onRunGoldenPath,
}: AgentProps) {
  const isFallback = result?.reasoning_is_fallback ?? null;

  return (
    <div className="view" data-testid="view-agent">
      <header className="view__header">
        <h1>Agent</h1>
        <p>
          Advisory only — the model explains decisions the policy engine has
          already made. It cannot authorize a payment, raise a limit, or
          suppress an escalation.
        </p>
      </header>

      <div className="agent__actions">
        {cases.map((c) => (
          <button
            key={c.key}
            type="button"
            className={`btn btn--${c.tone}`}
            onClick={() => c.isGoldenPath ? onRunGoldenPath() : onRunSample(c.build())}
            disabled={loading}
            data-testid={`agent-case-${c.key}`}
          >
            {loading && c.tone === "primary" && (
              <span className="btn__spinner" aria-hidden="true" />
            )}
            {c.label}
          </button>
        ))}
      </div>
      <p className="agent__actions-note">
        The three outlined cases each trip a different bounded stopping rule,
        so you can watch the agent refuse on purpose.
      </p>

      <ManualEntry disabled={loading} onSubmit={onRunSample} />

      {error && (
        <div className="banner banner--error" role="alert">
          <strong>Couldn't process the sample.</strong> {error}
        </div>
      )}

      {!result && !loading && !error && (
        <div className="empty-state" data-testid="agent-empty">
          <div className="empty-state__title">Nothing to explain yet</div>
          <div className="empty-state__description">
            Process a sample failure to see the model's reasoning alongside
            the deterministic decision it is explaining.
          </div>
        </div>
      )}

      {result && (
        <>
          <section className="card agent__summary">
            <div className="agent__summary-head">
              <div>
                <h2 className="card-title">Recovery outcome</h2>
                <p className="agent__ids data-mono">
                  {result.payment_id} · {result.event_id}
                </p>
              </div>
              <div className="agent__badges">
                <StatusBadge outcome={result.final_outcome} />
                {isFallback !== null && (
                  <span
                    className={`source-badge source-badge--${
                      isFallback ? "fallback" : "model"
                    }`}
                    data-testid="reasoning-source-badge"
                  >
                    {isFallback ? "Deterministic fallback" : "Model generated"}
                  </span>
                )}
                {isFallback && result.reasoning_fallback_reason && (
                  <span
                    className="source-badge source-badge--fallback"
                    data-testid="fallback-reason-badge"
                  >
                    {result.reasoning_fallback_reason.replace(/_/g, " ")}
                  </span>
                )}
              </div>
            </div>
            {result.reasoning_model && (
              <p className="agent__model data-mono">{result.reasoning_model}</p>
            )}
          </section>

          <div className="agent__panels">
            <AgentPanel
              title="Root cause, in plain language"
              body={result.root_cause_plain}
              empty="The model did not supply a plain-language root cause."
            />
            <AgentPanel
              title="Why this action is appropriate"
              body={result.why_appropriate}
              empty="No rationale supplied."
            />
            <AgentPanel
              title="Suggested customer message"
              body={result.customer_message}
              empty="No customer copy available. Copy is withheld when it names amounts or promises an outcome the policy has not authorized."
            />
            <AgentPanel
              title="Escalation summary"
              body={result.escalation_summary}
              empty="Not escalated."
            />
          </div>

          <section className="card agent__recovered">
            <h2 className="card-title">Measured result</h2>
            <p>
              <span className="agent__recovered-value data-mono">
                {formatRupees(result.amount_recovered ?? 0)}
              </span>{" "}
              recovered · payment {result.payment_status ?? "not attempted"}
              {result.simulated ? " (simulated)" : ""}
            </p>
          </section>

          <PipelineStages result={result} />
        </>
      )}
    </div>
  );
}

function AgentPanel({
  title,
  body,
  empty,
}: {
  title: string;
  body: string | null | undefined;
  empty: string;
}) {
  return (
    <section className="card agent__panel">
      <h2 className="card-title">{title}</h2>
      {body ? (
        <p className="agent__panel-body">{body}</p>
      ) : (
        <p className="agent__panel-empty">{empty}</p>
      )}
    </section>
  );
}
