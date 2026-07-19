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
  schema: 'nexus.ticket-closure-receipt.v1'
  ticket_id: number
  ticket_status: string
  ticket_revision: string
  scenario_key: string | null
  scenario_catalog_version: string | null
  scenario_catalog_sha256: string | null
  generated_at: string
  readiness: TicketClosureReadiness
  evidence: {
    ticket_event_ids: number[]
    background_job_ids: number[]
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
  schema: 'nexus.ticket-closure-evidence-result.v1'
  event_id: number
  evidence_sha256: string
  closure: TicketClosureReceipt
}
