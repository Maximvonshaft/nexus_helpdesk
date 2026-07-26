export type TicketClosureEvidenceKind = 'fact' | 'customer_input' | 'action' | 'outcome' | 'notification'
export type TicketClosureEvidenceState = 'verified' | 'completed' | 'waived' | 'failed'
export type TicketClosureEvidenceSource =
  | 'tracking'
  | 'provider_receipt'
  | 'operations_dispatch'
  | 'customer_confirmation'
  | 'policy_decision'
  | 'operator_observation'

export interface TicketClosureReadiness {
  scenario_key: string
  closure_ready: boolean
  missing_fact_classes: string[]
  missing_customer_inputs: string[]
  missing_action_classes: string[]
  missing_outcome_levels: string[]
  notification_satisfied: boolean
  blocked_reasons: string[]
}

export interface TicketClosureReceipt {
  schema: 'nexus.ticket-closure-receipt.v2'
  ticket_id: number
  ticket_status: string
  ticket_revision: string | null
  scenario_key: string | null
  scenario_catalog_version: string | null
  scenario_catalog_sha256: string | null
  generated_at: string
  readiness: TicketClosureReadiness
  evidence: {
    case_evidence_record_ids: number[]
    case_outcome_record_ids: number[]
    outbound_message_ids: number[]
    latest_material_at: string | null
    observation_elapsed: boolean
    contains_payloads: false
  }
  receipt_sha256: string
}

export interface TicketClosureEvidenceRequest {
  kind: TicketClosureEvidenceKind
  key: string
  state: TicketClosureEvidenceState
  source_kind: TicketClosureEvidenceSource
  source_ref: string
  source_revision: string
  observed_at: string
  note?: string | null
}

export interface TicketClosureEvidenceResult {
  schema: 'nexus.case-closure-evidence-result.v2'
  record_id: number
  record_type: 'case_evidence' | 'execution_attempt' | 'operational_outcome' | 'customer_notification'
  created: boolean
  evidence_sha256: string
  closure: TicketClosureReceipt
}
