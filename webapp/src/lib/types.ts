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
  title: string
  status: string
  priority: string
  customer_name?: string | null
  assignee_name?: string | null
  team_name?: string | null
  market_code?: string | null
  country_code?: string | null
  conversation_state?: string | null
  updated_at: string
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
  mime_type?: string | null
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

export interface QueueSummary {
  pending_outbound: number
  dead_outbound: number
  pending_jobs: number
  dead_jobs: number
  openclaw_links: number
}

export interface ServiceHealth {
  status?: string | null
  last_seen_at?: string | null
  instance_id?: string | null
  details?: Record<string, unknown> | null
}

export interface RuntimeHealth {
  sync_cursor?: string | null
  sync_daemon_last_seen_at?: string | null
  sync_daemon_status?: string | null
  stale_link_count: number
  pending_sync_jobs: number
  dead_sync_jobs: number
  pending_attachment_jobs: number
  dead_attachment_jobs: number
  worker?: ServiceHealth | null
  openclaw_sync_daemon?: ServiceHealth | null
  openclaw_event_daemon?: ServiceHealth | null
  queue?: {
    pending_outbound: number
    dead_outbound: number
    pending_jobs: number
    dead_jobs: number
  } | null
  openclaw?: {
    stale_link_count: number
    pending_sync_jobs: number
    dead_sync_jobs: number
  } | null
  warnings: string[]
}

export interface OpenClawConnectivityProbe {
  deployment_mode: string
  transport: string
  command?: string | null
  url?: string | null
  token_auth_configured: boolean
  password_auth_configured: boolean
  bridge_started: boolean
  conversations_tool_ok: boolean
  conversations_seen: number
  sample_session_key?: string | null
  level?: string
  transcript_read_ok?: boolean
  same_route_send_ready?: boolean
  attachment_metadata_ok?: boolean
  warnings: string[]
}

export interface ProductionReadiness {
  app_env: string
  database_url_scheme: string
  is_postgres: boolean
  storage_backend: string
  openclaw_transport: string
  metrics_enabled: boolean
  openclaw_sync_enabled: boolean
  status?: 'ready' | 'not_ready' | string
  checks?: Record<string, boolean>
  failures?: string[]
  warnings: string[]
}

export interface SignoffChecklist {
  status: 'ready' | 'not_ready'
  checks: Record<string, boolean>
  warnings: string[]
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
  published_summary?: string | null
  published_version: number
  published_at?: string | null
  created_at: string
  updated_at: string
}

export interface PersonaProfileList {
  profiles: PersonaProfile[]
  total: number
}

export interface KnowledgeItem {
  id: number
  item_key: string
  title: string
  summary?: string | null
  status: string
  source_type: string
  market_id?: number | null
  channel?: string | null
  audience_scope: string
  priority: number
  published_version: number
  published_at?: string | null
  created_at: string
  updated_at: string
}

export interface KnowledgeItemList {
  items: KnowledgeItem[]
  total: number
}

export interface ChannelOnboardingTask {
  id: number
  provider: string
  status: string
  requested_by?: number | null
  market_id?: number | null
  target_slot?: string | null
  desired_display_name?: string | null
  desired_channel_account_binding?: string | null
  openclaw_account_id?: string | null
  last_error?: string | null
  created_at: string
  updated_at: string
  started_at?: string | null
  completed_at?: string | null
}

export interface ChannelOnboardingTaskList {
  tasks: ChannelOnboardingTask[]
  total: number
}

export interface OpenClawUnresolvedEvent {
  id: number
  source: string
  session_key?: string | null
  event_type?: string | null
  recipient?: string | null
  source_chat_id?: string | null
  preferred_reply_contact?: string | null
  status: string
  replay_count: number
  last_error?: string | null
  created_at: string
  updated_at: string
}

export interface WebchatConversation {
  conversation_id: string
  ticket_id: number
  ticket_no: string
  title: string
  status: string
  visitor_name?: string | null
  visitor_email?: string | null
  visitor_phone?: string | null
  origin?: string | null
  page_url?: string | null
  last_seen_at?: string | null
  updated_at?: string | null
}

export interface WebchatMessage {
  id: number
  direction: 'visitor' | 'agent' | 'system' | string
  body: string
  author_label?: string | null
  created_at?: string | null
}

export interface WebchatThread {
  conversation_id: string
  ticket_id: number
  ticket_no: string
  origin?: string | null
  page_url?: string | null
  visitor: {
    name?: string | null
    email?: string | null
    phone?: string | null
    ref?: string | null
  }
  messages: WebchatMessage[]
}

export interface WebchatReplyResult {
  ok: boolean
  safety: {
    allowed: boolean
    level: 'allow' | 'review' | 'block' | string
    reasons: string[]
    requires_human_review: boolean
    normalized_body: string
  }
  message: WebchatMessage
}
