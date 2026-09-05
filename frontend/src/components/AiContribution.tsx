/**
 * What the AI actually contributed to a batch.
 *
 * Deliberately secondary to the recovery KPIs: this explains the model's
 * role, it does not claim credit for the money. Every number comes from
 * the batch response already fetched for the Overview — no extra call.
 */

import type { BatchSummary } from "../types/dashboard";

interface AiContributionProps {
  summary: BatchSummary | null;
}

export default function AiContribution({ summary }: AiContributionProps) {
  const r = summary?.reasoning;
  const rec = summary?.recommendation;
  if (!summary || !r) return null;

  const skipped = r.mode === "skipped" && (!rec || rec.mode === "skipped");

  return (
    <>
      <section className="card ai-card" data-testid="ai-contribution">
        <h2 className="card-title">What the AI agent actually did</h2>

        <div className="ai-card__grid">
          <div className="ai-metric">
            <span className="ai-metric__label">Consultations</span>
            <span className="ai-metric__value data-mono">{r.consultations}</span>
            <span className="ai-metric__note">
              every signal that reached the reasoning stage
            </span>
          </div>

          <div className="ai-metric">
            <span className="ai-metric__label">Real model answers</span>
            <span className="ai-metric__value data-mono">
              {r.model_generated} of {r.consultations}
            </span>
            <span className="ai-metric__note">
              {r.from_cache} cached, {r.fallback} fell back safely
            </span>
          </div>

          <div className="ai-metric">
            <span className="ai-metric__label">Chose the action</span>
            <span className="ai-metric__value data-mono">
              {r.chose_action ?? 0}
            </span>
            <span className="ai-metric__note">
              picked a different, equally authorised action from the policy's
              permitted set
            </span>
          </div>

          <div className="ai-metric">
            <span className="ai-metric__label">Policy overrides — must be 0</span>
            <span className="ai-metric__value data-mono">
              {r.overrode_policy}
            </span>
            <span className="ai-metric__note">
              the model cannot override a policy decision
            </span>
          </div>

          <div className="ai-metric">
            <span className="ai-metric__label">Customer messages drafted</span>
            <span className="ai-metric__value data-mono">
              {r.customer_messages}
            </span>
            <span className="ai-metric__note">
              withheld when they name amounts or promise an outcome
            </span>
          </div>

          {rec && (
            <>
              <div className="ai-metric">
                <span className="ai-metric__label">Recommendation model</span>
                <span className="ai-metric__value data-mono">
                  {rec.model}
                </span>
                <span className="ai-metric__note">
                  Prompt: {rec.prompt_version || "—"}
                </span>
              </div>

              <div className="ai-metric ai-metric--telemetry">
                <span className="ai-metric__label">Recommendation latency</span>
                <span className="ai-metric__value data-mono">
                  {rec.average_latency_ms
                    ? `${Math.round(rec.average_latency_ms)}ms avg`
                    : "—"}
                </span>
                <span className="ai-metric__note">
                  {rec.model_generated} live · {rec.fallback} fallback
                </span>
              </div>

              <div className="ai-metric">
                <span className="ai-metric__label">Policy treatment</span>
                <span className="ai-metric__value data-mono">
                  {rec.accepted} accepted · {rec.constrained} constrained
                </span>
                <span className="ai-metric__note">
                  {rec.rejected} rejected · {rec.unavailable} unavailable
                </span>
              </div>
            </>
          )}

          {r && !skipped && (
            <div className="ai-metric ai-metric--telemetry">
              <span className="ai-metric__label">Model Telemetry</span>
              <span className="ai-metric__value data-mono">
                {r.average_latency_ms ? `${Math.round(r.average_latency_ms)}ms avg` : "—"}
              </span>
              <span className="ai-metric__note">
                Prompt: {r.prompt_version || "—"} / Schema: {r.schema_version || "—"}
              </span>
            </div>
          )}
        </div>

        {skipped && (
          <p className="ai-card__note">
            This batch ran without live reasoning or recommendation NIM calls, so recommendations and
            explanations used deterministic fallbacks. AI is advisory and
            changes no outcome or amount — enable it in Run options for a
            smaller batch to see real model text.
          </p>
        )}
      </section>

      <section className="card ai-card" data-testid="ai-overrides">
        <h2 className="card-title">Where the AI changed the outcome</h2>

        {r.overrode_policy === 0 ? (
          <>
            <p className="ai-card__body">
              No consultation in this run overrode the deterministic decision —
              and none can. The reasoner copies the policy engine's verdict
              verbatim on both the model and fallback paths, so a model that
              replies “approve and retry” still cannot authorize anything.
            </p>
            <p className="ai-card__note">
              Every consultation is recorded on its own audit record, whether
              or not it changed anything, including whether the text came from
              the model or the deterministic fallback. See the Cases page for
              the full chain on any signal.
            </p>
          </>
        ) : (
          <>
            <p className="ai-card__body ai-card__body--alarm">
              {r.overrode_policy} consultation
              {r.overrode_policy === 1 ? "" : "s"} reported a verdict that
              differs from the policy engine's. That should be impossible —
              treat it as a safety-boundary failure, not a feature.
            </p>
            <p className="ai-card__note">
              Check the Cases page: each affected record carries the policy
              rule that decided it alongside the model's output.
            </p>
          </>
        )}
      </section>
    </>
  );
}
