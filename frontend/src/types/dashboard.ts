/**
 * Dashboard TypeScript types (TASK-009).
 *
 * These are display-only type definitions mirroring the backend
 * DashboardResponse. No policy logic is implemented here.
 */

/** Final outcome of a pipeline run — directly from backend enum. */
export type FinalOutcome =
  | "recovered"
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
}

/** Response from GET /api/dashboard/audit */
export interface AuditLogResponse {
  records: AuditRecord[];
  count: number;
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
