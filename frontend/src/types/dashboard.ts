/**
 * Dashboard TypeScript types (TASK-009).
 *
 * These are display-only type definitions mirroring the backend
 * DashboardResponse. No policy logic is implemented here.
 */

/** Final outcome of a pipeline run — directly from backend enum. */
export type FinalOutcome =
  | "recovered"
  | "pending"
  | "denied"
  | "escalated"
  | "execution_failed"
  | "recorded"
  | "audit_failed";

/** Response from POST /api/dashboard/process */
export interface DashboardResult {
  payment_id: string;
  event_id: string;

  // Classification
  failure_category: string | null;
  classification_reason: string | null;

  // AI recommendation (advisory only; never authorization)
  recommendation_success?: boolean | null;
  revenue_at_risk?: boolean | null;
  risk_score?: number | null;
  ai_suggested_cause?: string | null;
  ai_suggested_action?: string | null;
  ai_confidence?: number | null;
  recommendation_status?: string | null;
  recommendation_reason?: string | null;
  recommendation_model?: string | null;
  recommendation_latency_ms?: number | null;
  recommendation_prompt_version?: string | null;
  recommendation_is_fallback?: boolean | null;
  recommendation_fallback_reason?: string | null;

  // Policy (projected as-is from backend)
  policy_action: string | null;
  policy_reason: string | null;
  automatic_recovery_allowed: boolean | null;

  // Reasoning
  reasoning_recommendation: string | null;
  reasoning_explanation: string | null;
  reasoning_success: boolean | null;

  // Execution
  execution_status: string | null;
  execution_reason: string | null;

  // Escalation
  escalation_status: string | null;
  escalation_reason: string | null;
  escalation_severity: string | null;

  // Simulated payment result (never a real gateway confirmation)
  payment_status?: string | null;
  amount_recovered?: number | null;
  simulated?: boolean | null;

  // AI contribution — advisory only, cannot authorize anything
  reasoning_is_fallback?: boolean | null;
  reasoning_model?: string | null;
  reasoning_latency_ms?: number | null;
  reasoning_fallback_reason?: string | null;
  root_cause_plain?: string | null;
  why_appropriate?: string | null;
  customer_message?: string | null;
  escalation_summary?: string | null;

  // Final outcome
  final_outcome: FinalOutcome;

  // Metadata
  timestamp: string;
  amount: number | null;
  attempt_number: number | null;
  error: string | null;
}

/** A single audit log record from GET /api/dashboard/audit */
export interface AuditRecord {
  audit_id: string;
  event_id: string;
  payment_id: string;
  timestamp: string;
  classification_category: string | null;
  classification_reason: string | null;
  recommendation_success?: boolean | null;
  revenue_at_risk?: boolean | null;
  risk_score?: number | null;
  ai_suggested_cause?: string | null;
  ai_suggested_action?: string | null;
  ai_confidence?: number | null;
  recommendation_status?: string | null;
  recommendation_reason?: string | null;
  recommendation_model?: string | null;
  recommendation_latency_ms?: number | null;
  recommendation_prompt_version?: string | null;
  recommendation_revenue_at_risk?: boolean | null;
  recommendation_risk_score?: number | null;
  recommendation_suggested_cause?: string | null;
  recommendation_suggested_action?: string | null;
  recommendation_confidence?: number | null;
  recommendation_evidence?: string[];
  recommendation_is_fallback?: boolean | null;
  recommendation_fallback_reason?: string | null;
  policy_action: string | null;
  policy_reason: string | null;
  automatic_recovery_allowed: boolean | null;
  reasoning_success: boolean | null;
  reasoning_reference: string | null;
  execution_status: string | null;
  execution_reference: string | null;
  escalation_status: string | null;
  escalation_reference: string | null;
  final_outcome: FinalOutcome;
  error: string | null;
  attempt_number: number | null;
  amount: number | null;

  /** Decision chain: cause -> rule -> bound -> action -> outcome -> money. */
  classification_rule_id?: string | null;
  policy_rule_id?: string | null;
  amount_limit?: number | null;
  max_retries?: number | null;
  cooldown_seconds?: number | null;
  scheduled_for?: string | null;
  payment_status?: string | null;
  amount_recovered?: number | null;
  escalation_trigger?: string | null;
  reasoning_is_fallback?: boolean | null;
}

/** One failure category's money rollup within a batch. */
export interface ScenarioBreakdown {
  scenario: string;
  count: number;
  recovered_count: number;
  attempted_amount: number;
  recovered_amount: number;
  recovery_rate_amount: number;
}

/** Raw events narrowed to money actually recovered. Each stage is a real
 *  filter over the one above it, not a restatement. */
export interface FunnelCounts {
  raw: number;
  needed_signal: number;
  contacted: number;
  confirmed_recovered: number;
}

/** GET /api/dashboard/provider — configuration, never the key itself. */
export interface ProviderStatus {
  provider: string;
  model: string;
  base_url: string;
  configured: boolean;
}

/** How a batch produced its explanations. */
export interface BatchReasoning {
  mode: "model" | "skipped";
  /** Every event that reached the reasoning stage. */
  consultations: number;
  model_generated: number;
  fallback: number;
  from_cache: number;
  /** Drafts that passed the compliance filter. */
  customer_messages: number;
  /**
   * Times the model's verdict differed from the policy's. Structurally
   * always 0 — a non-zero value means the safety boundary was breached.
   */
  overrode_policy: number;
  model: string;
  prompt_version?: string | null;
  schema_version?: string | null;
  average_latency_ms?: number;
}

/** How the AI recommendation layer behaved during a batch. */
export interface BatchRecommendation {
  mode: "model" | "skipped";
  consultations: number;
  model_generated: number;
  fallback: number;
  risk_detected: number;
  accepted: number;
  constrained: number;
  rejected: number;
  unavailable: number;
  model: string;
  prompt_version?: string | null;
  average_latency_ms?: number;
}

export interface SchedulerSummary {
  ran: number;
  recovered: number;
  failed: number;
  amount_recovered?: number;
  job_ids: string[];
}

/** Executor-level action breakdown for a batch run. Distinct from the
 *  funnel (which measures signal-filtering stages). */
export interface RecoveryActions {
  retries_attempted: number;
  payments_recovered: number;
  payments_pending: number;
  payments_escalated: number;
  execution_failed: number;
}

/** Response from POST /api/dashboard/run-batch */
export interface BatchSummary {
  transactions_processed: number;
  total_attempted_amount: number;
  total_recovered_amount: number;
  recovery_rate_by_amount: number;
  recovery_rate_by_count: number;
  outcomes: Record<string, number>;
  funnel: FunnelCounts;
  by_scenario: ScenarioBreakdown[];
  audit_ids: string[];
  reasoning?: BatchReasoning;
  recommendation?: BatchRecommendation;
  recovery_actions?: RecoveryActions;
  scheduler: SchedulerSummary | null;
  simulated?: boolean;
  duration_seconds: number;
}

export interface ScheduledJob {
  job_id: string;
  payment_id: string;
  event_id: string;
  action: string;
  next_eligible_at: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ScheduledJobsResponse {
  jobs: ScheduledJob[];
  count: number;
}

export interface ResetResponse {
  cleared: boolean;
  recovery_state_cleared: boolean;
  audit_log_preserved: boolean;
  note: string;
}

/** Response from GET /api/dashboard/audit */
export interface AuditLogResponse {
  records: AuditRecord[];
  /** Records in this page. */
  count: number;
  /** Total matching records, ignoring pagination. */
  total?: number;
}

/** Payload for POST /api/dashboard/process */
export interface PaymentEventPayload {
  event_id: string;
  razorpay_payment_id: string;
  merchant_id: string;
  customer_id: string;
  type: "one_time" | "subscription";
  amount: number;
  currency: string;
  payment_method: "upi" | "card" | "netbanking";
  error_code: string;
  error_description: string;
  failure_category: string;
  attempt_number: number;
  mandate_status: string | null;
  timestamp: string;
}

/** Revenue-at-risk rollups from GET /api/dashboard/risk. */
export interface MerchantRisk {
  merchant_id: string;
  failures: number;
  at_risk_amount: number;
  recovered_amount: number;
  outstanding_amount: number;
}

export interface RepeatCustomer {
  /** Pseudonymous reference — the raw customer id is never stored. */
  customer_ref: string;
  failures: number;
  at_risk_amount: number;
}

export interface SubscriptionRisk {
  count: number;
  at_risk_amount: number;
  mandate_issues: number;
}

export interface RiskSummary {
  total_at_risk_amount: number;
  total_recovered_amount: number;
  outstanding_amount: number;
  records_considered: number;
  by_merchant: MerchantRisk[];
  repeat_customers: RepeatCustomer[];
  subscription_failures: SubscriptionRisk;
}
