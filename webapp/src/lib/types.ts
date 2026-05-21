export type BadgeTone = 'default' | 'warning' | 'success' | 'danger'

export interface AuthUser {
  id: number
  username: string
  display_name: string
  email?: string | null
  role: string
  team_id?: number | null
  capabilities?: string[]
}

export interface AdminUser extends AuthUser {
  is_active: boolean
  capabilities: string[]
  created_at: string
  updated_at: string
}

export interface Market {
  id: number
  code: string
  name: string
  country_code?: string | null
  language_code?: string | null
  timezone?: string | null
}

export interface Team {
  id: number
  name: string
  team_type: string
  market_id?: number | null
}

export interface LiteMeta {
  users: AuthUser[]
  teams: Team[]
  statuses: string[]
  priorities: string[]
}

export interface CaseListItem {
  id: number
  ticket_no?: string | null
  title: string
  status: string
  priority: string
  source_channel?: string | null
  category?: string | null
  sub_category?: string | null
  tracking_number?: string | null
  customer_name?: string | null
  assignee_name?: string | null
  team_name?: string | null
  market_id?: number | null
  market_code?: string | null
  country_code?: string | null
  conversation_state?: string | null
  updated_at: string
  resolution_due_at?: string | null
  overdue?: boolean
}

export interface CaseListPage {
  items: CaseListItem[]
  next_cursor: string | null
  has_more: boolean
  filters?: Record<string, unknown>
}

export interface TranscriptMessage {
  id: number
  role: string
  author_name?: string | null
  body_text?: string | null
  created_at?: string
  received_at?: string
}

export interface AttachmentReference {
  id: number
  ticket_id: number
  transcript_message_id: number
  remote_attachment_id: string
  content_type?: string | null
  filename?: string | null
  storage_status: string
  storage_key?: string | null
  created_at: string
}

export interface SystemAttachment {
  id: number
  file_name: string
  download_url?: string | null
  mime_type?: string | null
  file_size?: number | null
  visibility?: string
  created_at: string
}

export interface Bulletin {
  id: number
  market_id?: number | null
  country_code?: string | null
  title: string
  body: string
  summary?: string | null
  category?: string | null
  channels_csv?: string | null
  audience?: string | null
  severity?: string | null
  auto_inject_to_ai?: boolean
  is_active: boolean
  starts_at?: string | null
  ends_at?: string | null
  created_at?: string
  updated_at?: string
}

export interface ChannelAccount {
  id: number
  provider: string
  account_id: string
  display_name?: string | null
  market_id?: number | null
  is_active: boolean
  priority: number
  health_status: string
  fallback_account_id?: string | null
  updated_at: string
}

export interface EvidenceSummary {
  loaded: boolean
  preview_limit: number
  attachments_count: number
  openclaw_transcript_count: number
  openclaw_attachment_references_count: number
  active_market_bulletins_count: number
}

export interface CaseDetail {
  id: number
  title: string
  status: string
  priority: string
  market_code?: string | null
  country_code?: string | null
  conversation_state?: string | null
  customer_name?: string | null
  customer_request?: string | null
  issue_summary?: string | null
  last_customer_message?: string | null
  preferred_reply_channel?: string | null
  preferred_reply_contact?: string | null
  tracking_number?: string | null
  destination_country?: string | null
  assignee_name?: string | null
  team_name?: string | null
  updated_at: string
  ai_summary?: string | null
  ai_classification?: string | null
  ai_confidence?: number | null
  required_action?: string | null
  missing_fields?: string | null
  customer_update?: string | null
  resolution_summary?: string | null
  evidence_summary?: EvidenceSummary
  attachments_count?: number
  openclaw_transcript_count?: number
  openclaw_attachment_references_count?: number
  active_market_bulletins_count?: number
  openclaw_conversation?: {
    session_key: string
    channel?: string | null
    recipient?: string | null
    account_id?: string | null
    thread_id?: string | null
    last_synced_at?: string | null
  } | null
  openclaw_transcript?: TranscriptMessage[]
  attachments?: SystemAttachment[]
  openclaw_attachment_references?: AttachmentReference[]
  active_market_bulletins?: Bulletin[]
  customer?: {
    name?: string | null
    phone?: string | null
    email?: string | null
  } | null
}

export interface SpeedafActionResponse {
  ok: boolean
  status: string
  message: string
  jobId?: number | null
  dedupeKey?: string | null
}

export interface SpeedafCancelPreviewResponse {
  ok: boolean
  cancelAllowed: boolean
  currentStatus?: string | null
  currentStatusLabel?: string | null
  reason?: string | null
  reasonLabel?: string | null
  confirmToken?: string | null
  expiresInSeconds?: number | null
}

export interface QueueSummary {
  pending_outbound: number
  dead_outbound: number
  external_pending_outbound?: number
  external_dead_outbound?: number
  webchat_local_ack_sent?: number
  webchat_ai_delivered_sent?: number
  webchat_ai_safe_fallback_sent?: number
  webchat_card_sent?: number
  webchat_handoff_ack_sent?: number
  pending_jobs: number
  dead_jobs: number
  openclaw_links: number
}

export interface RuntimeHealth {
  sync_cursor?: string | null
  sync_daemon_last_seen_at?: string | null
  sync_daemon_status?: string | null
  stale_link_count: number
  openclaw_links_count?: number
  transcript_messages_count?: number
  unresolved_events_count?: number
  pending_sync_jobs: number
  dead_sync_jobs: number
  pending_attachment_jobs: number
  dead_attachment_jobs: number
  external_pending_outbound?: number
  external_dead_outbound?: number
  webchat_local_ack_sent?: number
  webchat_ai_delivered_sent?: number
  webchat_ai_safe_fallback_sent?: number
  webchat_card_sent?: number
  webchat_handoff_ack_sent?: number
  outbound_dispatch_enabled?: boolean
  outbound_provider?: string
  openclaw_bridge_allow_writes?: boolean
  openclaw_cli_fallback_enabled?: boolean
  warnings: string[]
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

export interface OpenClawConnectivityProbe {
  deployment_mode: string
  base_url?: string | null
  bridge_url?: string | null
  ok: boolean
  status?: string | null
  latency_ms?: number | null
  error?: string | null
}

export interface WebchatConversation {
  id: number
  session_id: string
  visitor_name?: string | null
  visitor_email?: string | null
  visitor_phone?: string | null
  status: string
  ticket_id?: number | null
  created_at: string
  updated_at: string
}

export interface WebchatThread {
  ticket: CaseDetail
  conversation?: WebchatConversation | null
  messages: Array<Record<string, unknown>>
}

export interface WebchatReplyResult {
  ok: boolean
  message_id?: number | null
  status?: string | null
  delivery_semantics?: string | null
}

export interface WebchatVoiceRuntimeConfig {
  enabled: boolean
  provider?: string | null
  server_url?: string | null
  public_base_url?: string | null
}

export interface WebchatVoiceSession {
  id: string
  ticket_id?: number | null
  status: string
  accepted_by_user_id?: number | null
  created_at?: string | null
  updated_at?: string | null
}

export interface BackgroundJob {
  id: number
  queue_name: string
  job_type: string
  status: string
  attempt_count: number
  max_attempts: number
  last_error?: string | null
  created_at: string
  updated_at: string
}

export interface ProductionReadiness {
  ok: boolean
  status: string
  checks?: Record<string, unknown>
  warnings?: string[]
}

export interface SignoffChecklist {
  ok: boolean
  checks: Array<Record<string, unknown>>
}

export interface RuntimeRecoveryResult {
  ok: boolean
  requeued?: number
  job_id?: number
  message_id?: number
  status?: string
  job_type?: string | null
}

export interface OpenClawUnresolvedEvent {
  id: number
  source?: string | null
  session_key?: string | null
  status: string
  failure_reason?: string | null
  created_at: string
}

export interface AIConfigResource {
  id: number
  name: string
  config_type: string
  is_active: boolean
}

export interface AIConfigVersion {
  id: number
  version: number
  notes?: string | null
  created_at?: string
}

export interface KnowledgeItemList {
  items: Array<Record<string, unknown>>
}

export interface PersonaProfileList {
  items: Array<Record<string, unknown>>
}

export interface ChannelOnboardingTaskList {
  items: Array<Record<string, unknown>>
}