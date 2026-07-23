export interface ProviderRuntimeProviderStatus {
  name: string
  selected: boolean
  feature_enabled: boolean
  configured: boolean
  runtime: string
  capabilities: Record<string, boolean>
  diagnostics?: {
    primary_provider?: string
    base_url_configured?: boolean
    rag_base_url_configured?: boolean
    rag_runtime_isolated?: boolean
    allow_shared_rag_model?: boolean
    token_file_configured?: boolean
    inline_token_configured?: boolean
    chat_mode?: string
    direct_path?: string
    rag_path?: string
    request_shape?: string
    direct_model?: string
    rag_model?: string
    timeout_seconds?: string | number
  }
}
export interface ProviderRuntimeStatus {
  ok: boolean
  status: string
  app_env?: string
  webchat_runtime_enabled?: boolean
  configured_provider?: string
  fallback_provider?: string | null
  providers: ProviderRuntimeProviderStatus[]
  warnings: string[]
  boundary?: {
    secret_values_exposed: boolean
    external_network_call: boolean
    customer_message_sent: boolean
  }
}
export interface OutboundChannelCapability {
  channel: string
  label: string
  dispatch_type: string
  status: string
  customer_sendable: boolean
  enabled: boolean
  configured: boolean
  account_required: boolean
  target_required: boolean
  supports_send: boolean
  supports_inbound_sync: boolean
  supports_delivery_receipt: boolean
  supports_attachments: boolean
  external_send: boolean
  target_validation?: string | null
  missing: string[]
  operator_note?: string | null
}
export interface OutboundChannelCapabilitiesResponse {
  channels: OutboundChannelCapability[]
}
export interface ProductionReadiness {
  app_env: string
  database_url_scheme: string
  is_postgres: boolean
  storage_backend: string
  metrics_enabled: boolean
  outbound_email_production_pilot_enabled?: boolean
  outbound_email_active_accounts?: number
  outbound_email_successful_test_send_accounts?: number
  outbound_email_test_send_max_age_hours?: number
  warnings: string[]
}
export interface SignoffChecklist {
  status: 'ready' | 'not_ready'
  checks: Record<string, boolean>
  warnings: string[]
}
export type ReleaseReadinessProfile = 'controlled' | 'provider_canary' | 'full'
export interface ReleaseReadinessCollector {
  status?: string
  reason_codes?: string[]
  [key: string]: unknown
}
export interface ReleaseReadiness {
  schema: string
  profile: ReleaseReadinessProfile
  status: 'ready' | 'not_ready'
  reason_codes: string[]
  collectors: Record<string, ReleaseReadinessCollector>
  production_authorized: boolean
  provider_enablement_authorized: boolean
  webchat_ai_enablement_authorized: boolean
  voice_enablement_authorized: boolean
  outbound_enablement_authorized: boolean
  operations_enablement_authorized: boolean
}
export interface BackgroundJob {
  id: number
  queue_name: string
  job_type: string
  status: string
  dedupe_key?: string | null
  attempt_count: number
  max_attempts: number
  next_run_at?: string | null
  locked_at?: string | null
  locked_by?: string | null
  last_error?: string | null
  created_at: string
  updated_at: string
}
export interface AIConfigResource {
  id: number
  resource_key: string
  config_type: string
  name: string
  description?: string | null
  scope_type: string
  scope_value?: string | null
  market_id?: number | null
  is_active: boolean
  draft_summary?: string | null
  draft_content_json?: Record<string, unknown> | null
  published_summary?: string | null
  published_content_json?: Record<string, unknown> | null
  published_version: number
  published_at?: string | null
  created_at: string
  updated_at: string
}
export interface AIConfigVersion {
  id: number
  resource_id: number
  version: number
  snapshot_json: Record<string, unknown>
  summary?: string | null
  notes?: string | null
  published_by?: number | null
  published_at: string
}
export interface PersonaProfile {
  id: number
  profile_key: string
  name: string
  description?: string | null
  market_id?: number | null
  channel?: string | null
  language?: string | null
  is_active: boolean
  draft_summary?: string | null
  draft_content_json?: Record<string, unknown> | null
  published_summary?: string | null
  published_content_json?: Record<string, unknown> | null
  published_version: number
  published_at?: string | null
  created_at: string
  updated_at: string
}
export interface PersonaProfileVersion {
  id: number
  profile_id: number
  version: number
  snapshot_json: Record<string, unknown>
  summary?: string | null
  notes?: string | null
  published_by?: number | null
  published_at: string
}
export interface PersonaProfileReview {
  id: number
  profile_id: number
  review_version: number
  status: string
  snapshot_json: Record<string, unknown>
  summary?: string | null
  notes?: string | null
  requested_by?: number | null
  requested_at: string
  reviewed_by?: number | null
  reviewed_at?: string | null
  decision_note?: string | null
  release_window_start?: string | null
  release_window_end?: string | null
  published_by?: number | null
  published_version?: number | null
  published_at?: string | null
  created_at: string
  updated_at: string
}
export interface PersonaProfileReviewList {
  reviews: PersonaProfileReview[]
  total: number
}
export interface PersonaProfileDetail extends PersonaProfile {
  versions: PersonaProfileVersion[]
}
export interface PersonaProfileList {
  profiles: PersonaProfile[]
  total: number
}
export interface PersonaResolvePreviewResult {
  profile?: PersonaProfile | null
  match_rank?: number | null
}
export interface PersonaRuntimeEvidenceResult {
  generated_at: string
  matched_profile_key?: string | null
  match_rank?: number | null
  expected_profile_key?: string | null
  matched_expected?: boolean | null
  persona_context?: Record<string, unknown> | null
  runtime_context: Record<string, unknown>
  evidence: Record<string, unknown>
}
