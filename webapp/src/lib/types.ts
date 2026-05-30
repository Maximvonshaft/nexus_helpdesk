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

export interface TodayWorkbenchTask {
  key: string
  title: string
  count: number | string
  severity: BadgeTone
  source: string
  next: string
  target: string
  href: string
  enabled: boolean
}

export interface TodayWorkbenchMetric {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}

export interface TodayWorkbenchSlaPriority {
  ticket_id: number
  ticket_no?: string | null
  title: string
  priority: string
  status: string
  source_channel?: string | null
  customer_name?: string | null
  assignee_name?: string | null
  team_name?: string | null
  resolution_due_at?: string | null
  first_response_due_at?: string | null
  minutes_to_due?: number | null
  overdue: boolean
  href: string
}

export interface TodayWorkbenchInteractionState {
  key: string
  state: string
  user_copy: string
  required: string
  status: string
}

export interface TodayWorkbenchCommand {
  key: string
  label: string
  role: string
  target: string
  href: string
  next: string
  enabled: boolean
  capability: string
}

export interface TodayWorkbench {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  tasks: TodayWorkbenchTask[]
  metrics: TodayWorkbenchMetric[]
  sla_priorities: TodayWorkbenchSlaPriority[]
  interaction_states: TodayWorkbenchInteractionState[]
  command_center: TodayWorkbenchCommand[]
}

export interface ControlTowerKpi {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}

export interface ControlTowerAction {
  key: string
  label: string
  count: number
  tone: BadgeTone
  next: string
  href: string
  capability: string
  enabled: boolean
  action_task_id?: number | null
  action_status?: string | null
}

export interface ControlTowerTeamWorkload {
  team_id?: number | null
  team_name: string
  active_tickets: number
  unassigned: number
  sla_risk: number
  overdue: number
}

export interface ControlTowerChannelHealth {
  key: string
  label: string
  health: BadgeTone
  queue: number
  risk: number
  href: string
  capability: string
  enabled: boolean
}

export interface ControlTowerBulletinImpact {
  severity: string
  category: string
  count: number
  tone: BadgeTone
}

export interface ControlTowerGovernanceLane {
  key: string
  area: string
  value: number
  risk: BadgeTone
  next: string
  href: string
  capability: string
  enabled: boolean
}

export interface ControlTowerTemplateBlock {
  key: string
  label: string
  backend_contract: string
  status: string
  evidence: string
  href: string
}

export interface ControlTower {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  kpis: ControlTowerKpi[]
  manager_actions: ControlTowerAction[]
  team_workload: ControlTowerTeamWorkload[]
  channel_health: ControlTowerChannelHealth[]
  bulletin_impact: ControlTowerBulletinImpact[]
  governance_lanes: ControlTowerGovernanceLane[]
  template_blocks: ControlTowerTemplateBlock[]
  facts: Record<string, number | string | string[]>
}

export interface ControlTowerActionResult {
  ok: boolean
  task_id: number
  created: boolean
  status: string
  action_key: string
  submitted_at: string
}

export interface QATrainingKpi {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}

export interface QATrainingQueueItem {
  key: string
  channel: string
  sample: string
  ticket_id: number
  ticket_no?: string | null
  customer_name?: string | null
  agent_name?: string | null
  ai_pre_score: number
  risk: string
  feedback: string
  agent_appeal: string
  appeal_status?: string | null
  appeal_task_id?: number | null
  source: string
  created_at?: string | null
  href: string
  evidence: string[]
}

export interface QATrainingScorecardRow {
  key: string
  criterion: string
  score: number
  tone: BadgeTone
  evidence: string
  next: string
}

export interface QATrainingTask {
  key: string
  title: string
  owner: string
  priority: number
  status: string
  source: string
  next: string
  href: string
  enabled: boolean
  capability: string
}

export interface QATrainingKnowledgeGap {
  key: string
  title: string
  source: string
  status: string
  owner: string
  next: string
  href: string
  evidence: string
  resource_id?: number | null
  ticket_id?: number | null
  sample_key?: string | null
  channel?: string | null
  sample?: string | null
}

export interface QATrainingLoopStep {
  key: string
  step: string
  owner: string
  artifact: string
  status: string
  href: string
  enabled: boolean
}

export interface QATrainingTemplateBlock {
  key: string
  label: string
  backend_contract: string
  status: string
  evidence: string
  href: string
}

export interface QATraining {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  kpis: QATrainingKpi[]
  qa_queue: QATrainingQueueItem[]
  scorecard: QATrainingScorecardRow[]
  training_tasks: QATrainingTask[]
  knowledge_gaps: QATrainingKnowledgeGap[]
  loop_steps: QATrainingLoopStep[]
  template_blocks: QATrainingTemplateBlock[]
  facts: Record<string, number | string | boolean>
}

export interface QATrainingAppealResult {
  ok: boolean
  task_id: number
  created: boolean
  status: string
  ticket_id: number
  sample_key: string
  appeal_status: string
  submitted_at: string
}

export interface QATrainingKnowledgeGapResult {
  ok: boolean
  resource_id: number
  resource_key: string
  task_id: number
  created: boolean
  status: string
  ticket_id?: number | null
  gap_key: string
  submitted_at: string
}

export interface KnowledgeStudioKpi {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}

export interface KnowledgeStudioItem {
  id: number
  item_key: string
  title: string
  status: string
  source_type: string
  knowledge_kind: string
  channel?: string | null
  audience_scope: string
  language?: string | null
  priority: number
  parsing_status: string
  fact_status: string
  answer_mode: string
  published_version: number
  indexed_version: number
  chunk_count: number
  draft_ready: boolean
  publish_ready: boolean
  retrieval_test_ready: boolean
  has_conflict: boolean
  updated_at?: string | null
  href: string
  evidence: string
}

export interface KnowledgeStudioConflict {
  key: string
  term: string
  scope: string
  item_ids?: number[]
  item_keys: string[]
  titles: string[]
  status: string
  blocker: boolean
  href: string
  evidence?: string[]
}

export interface KnowledgeStudioLifecycleStep {
  key: string
  step: string
  owner: string
  artifact: string
  status: string
  count: number
  href: string
  enabled: boolean
}

export interface KnowledgeStudioTemplateBlock {
  key: string
  label: string
  backend_contract: string
  status: string
  evidence: string
  href: string
}

export interface KnowledgeStudio {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  kpis: KnowledgeStudioKpi[]
  items: KnowledgeStudioItem[]
  conflicts: KnowledgeStudioConflict[]
  release_lifecycle: KnowledgeStudioLifecycleStep[]
  template_blocks: KnowledgeStudioTemplateBlock[]
  facts: Record<string, number | string | boolean>
}

export interface PersonaBuilderKpi {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}

export interface PersonaBuilderProfile {
  id: number
  profile_key: string
  name: string
  description?: string | null
  market_id?: number | null
  channel?: string | null
  language?: string | null
  scope_label: string
  scope_specificity: number
  is_active: boolean
  published_version: number
  draft_ready: boolean
  published_ready: boolean
  needs_publish: boolean
  identity_ready: boolean
  boundary_ready: boolean
  guardrail_count: number
  risk_flags: string[]
  updated_at?: string | null
  href: string
  evidence: string
}

export interface PersonaBuilderReview {
  id: number
  profile_id: number
  profile_key?: string | null
  profile_name?: string | null
  review_version: number
  status: string
  summary?: string | null
  notes?: string | null
  scope_label: string
  requested_by?: number | null
  requested_at?: string | null
  reviewed_by?: number | null
  reviewed_at?: string | null
  decision_note?: string | null
  release_window_start?: string | null
  release_window_end?: string | null
  published_by?: number | null
  published_version?: number | null
  published_at?: string | null
  href: string
  evidence: string
}

export interface PersonaBuilderSimulationScenario {
  market_id?: number | null
  channel?: string | null
  language?: string | null
  matched_profile_key?: string | null
  matched_name?: string | null
  match_rank?: number | null
  published_version?: number | null
  reasons: string[]
  fallback: boolean
  status: string
  href: string
}

export interface PersonaBuilderLifecycleStep {
  key: string
  step: string
  owner: string
  artifact: string
  status: string
  count: number
  href: string
  enabled: boolean
}

export interface PersonaBuilderTemplateBlock {
  key: string
  label: string
  backend_contract: string
  status: string
  evidence: string
  href: string
}

export interface PersonaBuilder {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  kpis: PersonaBuilderKpi[]
  profiles: PersonaBuilderProfile[]
  approval_queue: PersonaBuilderReview[]
  simulation_scenarios: PersonaBuilderSimulationScenario[]
  release_lifecycle: PersonaBuilderLifecycleStep[]
  template_blocks: PersonaBuilderTemplateBlock[]
  facts: Record<string, number | string | boolean>
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

export interface BulletinImpactPreviewPayload {
  market_id?: number | null
  country_code?: string | null
  channels_csv?: string | null
  audience?: string | null
  auto_inject_to_ai?: boolean
  is_active?: boolean
  starts_at?: string | null
  ends_at?: string | null
}

export interface BulletinImpactChannelCount {
  channel: string
  count: number
}

export interface BulletinImpactTicket {
  id: number
  ticket_no: string
  title: string
  status: string
  channel: string
  updated_at: string
}

export interface BulletinImpactPreview {
  matching_tickets: number
  ready_to_reply_tickets: number
  channel_counts: BulletinImpactChannelCount[]
  sample_tickets: BulletinImpactTicket[]
  window_status: string
  scope_label: string
  auto_inject_to_ai: boolean
  ai_context_enabled: boolean
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

export type OutboundEmailSecurityMode = 'starttls' | 'ssl' | 'plain'

export interface OutboundEmailAccount {
  id: number
  display_name?: string | null
  host: string
  port: number
  username: string
  from_address: string
  reply_to?: string | null
  security_mode: OutboundEmailSecurityMode | string
  inbound_enabled: boolean
  imap_host?: string | null
  imap_port?: number | null
  imap_username?: string | null
  imap_security_mode?: OutboundEmailSecurityMode | string | null
  imap_mailbox?: string | null
  imap_sync_cursor?: string | null
  imap_last_seen_at?: string | null
  imap_last_status?: string | null
  imap_last_error?: string | null
  imap_last_sync_job_id?: number | null
  imap_password_configured: boolean
  imap_password_mask?: string | null
  market_id?: number | null
  is_active: boolean
  priority: number
  health_status: string
  last_test_status?: string | null
  last_test_error?: string | null
  last_test_at?: string | null
  password_configured: boolean
  password_mask?: string | null
  created_at: string
  updated_at: string
}

export type OutboundEmailAccountCreate = {
  display_name?: string | null
  host: string
  port: number
  username: string
  password: string
  from_address: string
  reply_to?: string | null
  security_mode: OutboundEmailSecurityMode
  inbound_enabled?: boolean
  imap_host?: string | null
  imap_port?: number | null
  imap_username?: string | null
  imap_password?: string | null
  imap_security_mode?: OutboundEmailSecurityMode | null
  imap_mailbox?: string | null
  market_id?: number | null
  priority?: number
  is_active?: boolean
}

export type OutboundEmailAccountUpdate = Partial<{
  display_name: string | null
  host: string
  port: number
  username: string
  password: string
  from_address: string
  reply_to: string | null
  security_mode: OutboundEmailSecurityMode
  inbound_enabled: boolean
  imap_host: string | null
  imap_port: number | null
  imap_username: string | null
  imap_password: string
  imap_security_mode: OutboundEmailSecurityMode | null
  imap_mailbox: string | null
  market_id: number | null
  priority: number
  is_active: boolean
}>

export type OutboundEmailTestSendRequest = {
  to_address: string
  subject?: string | null
  body?: string | null
}

export interface OutboundEmailTestSendResult {
  ok: boolean
  account_id: number
  provider_status: string
  failure_code?: string | null
  error_message?: string | null
  sent_at?: string | null
  health_status: string
}

export type EmailDeliveryReceiptPayload = {
  delivery_status: 'accepted' | 'delivered' | 'opened' | 'deferred' | 'bounced' | 'failed' | 'rejected' | 'complained'
  provider?: string | null
  provider_event_type?: string | null
  provider_event_id?: string | null
  provider_status?: string | null
  provider_message_id?: string | null
  mailbox_message_id?: string | null
  detail?: string | null
  failure_code?: string | null
  failure_reason?: string | null
  occurred_at?: string | null
  raw_payload?: Record<string, unknown> | null
}

export interface EmailDeliveryReceiptResult {
  ok: boolean
  created: boolean
  message_id: number
  ticket_id: number
  status: string
  provider_status?: string | null
  delivery_status: string
  delivery_event_type?: string | null
  delivery_receipt_provider?: string | null
  delivery_receipt_id?: string | null
  delivery_receipt_at?: string | null
  delivery_detail?: string | null
  failure_code?: string | null
  failure_reason?: string | null
  ticket_event_id?: number | null
  audit_id?: number | null
}

export interface EmailMailboxQueueItem {
  id: number
  ticket_id: number
  ticket_no?: string | null
  title: string
  status: string
  priority: string
  source_channel?: string | null
  category?: string | null
  sub_category?: string | null
  tracking_number?: string | null
  customer_name?: string | null
  customer_email?: string | null
  assignee_name?: string | null
  team_name?: string | null
  market_id?: number | null
  market_code?: string | null
  country_code?: string | null
  conversation_state?: string | null
  updated_at: string
  resolution_due_at?: string | null
  overdue: boolean
  queue_source: 'inbound_email' | 'outbound_message' | 'ticket_marker'
  queue_reason: string
  direction: 'inbound' | 'outbound' | 'ticket'
  last_message_at?: string | null
  last_message_subject?: string | null
  last_message_preview?: string | null
  mailbox_thread_id?: string | null
  mailbox_message_id?: string | null
  mailbox_references?: string | null
  provider?: string | null
  provider_status?: string | null
  delivery_status?: string | null
  outbound_message_id?: number | null
  inbound_message_id?: number | null
}

export interface EmailMailboxQueueResponse {
  generated_at: string
  source: 'mailbox_projection'
  items: EmailMailboxQueueItem[]
  total: number
  filters: Record<string, unknown>
}

export interface EmailMailboxSyncAccountStatus {
  account_id: number
  display_name?: string | null
  from_address: string
  inbound_enabled: boolean
  configured: boolean
  imap_host?: string | null
  imap_mailbox?: string | null
  imap_sync_cursor?: string | null
  imap_last_seen_at?: string | null
  imap_last_status?: string | null
  imap_last_error?: string | null
  imap_last_sync_job_id?: number | null
}

export interface EmailMailboxSyncStatus {
  generated_at: string
  daemon_enabled: boolean
  interval_seconds: number
  enabled_accounts: number
  configured_accounts: number
  pending_jobs: number
  dead_jobs: number
  accounts: EmailMailboxSyncAccountStatus[]
}

export interface EmailMailboxSyncEnqueueResult {
  ok: boolean
  enqueued: number
  job_ids: number[]
}

export type OutboundSendPayload = {
  channel: string
  subject?: string | null
  body: string
  attachment_ids?: number[]
}

export type InboundEmailPayload = {
  from_address: string
  from_name?: string | null
  to_address?: string | null
  cc?: string | null
  subject?: string | null
  body: string
  provider?: string | null
  provider_message_id?: string | null
  mailbox_thread_id?: string | null
  mailbox_message_id?: string | null
  mailbox_references?: string | null
  in_reply_to?: string | null
  received_at?: string | null
}

export interface InboundEmailIngestResult {
  ok: boolean
  created: boolean
  ticket_event_id?: number | null
  audit_id?: number | null
  message: {
    id: number
    ticket_id: number
    source: string
    provider: string
    provider_message_id?: string | null
    from_address: string
    from_name?: string | null
    to_address?: string | null
    cc?: string | null
    subject?: string | null
    body_preview?: string | null
    mailbox_thread_id: string
    mailbox_message_id?: string | null
    mailbox_references?: string | null
    in_reply_to?: string | null
    received_at: string
    created_at: string
  }
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
  transport: string
  command?: string | null
  url?: string | null
  token_auth_configured: boolean
  password_auth_configured: boolean
  bridge_started: boolean
  conversations_tool_ok: boolean
  conversations_seen: number
  sample_session_key?: string | null
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

export interface KnowledgeItem {
  id: number
  item_key: string
  title: string
  summary?: string | null
  status: string
  source_type: string
  knowledge_kind: string
  market_id?: number | null
  channel?: string | null
  audience_scope: string
  language?: string | null
  priority: number
  starts_at?: string | null
  ends_at?: string | null
  source_url?: string | null
  file_name?: string | null
  file_storage_key?: string | null
  mime_type?: string | null
  file_size?: number | null
  parsing_status?: string | null
  parsing_error?: string | null
  parsed_at?: string | null
  indexed_version: number
  indexed_at?: string | null
  chunk_count: number
  fact_question?: string | null
  fact_answer?: string | null
  fact_aliases_json?: string[] | null
  fact_status?: string | null
  answer_mode?: string | null
  citation_metadata_json?: Record<string, unknown> | null
  draft_body?: string | null
  draft_normalized_text?: string | null
  published_body?: string | null
  published_normalized_text?: string | null
  published_version: number
  published_at?: string | null
  created_at: string
  updated_at: string
}

export interface KnowledgeItemVersion {
  id: number
  item_id: number
  version: number
  snapshot_json: Record<string, unknown>
  summary?: string | null
  notes?: string | null
  published_by?: number | null
  published_at: string
}

export interface KnowledgeItemDetail extends KnowledgeItem {
  versions: KnowledgeItemVersion[]
}

export interface KnowledgeItemList {
  items: KnowledgeItem[]
  total: number
}

export interface KnowledgeChunkHit {
  item_id: number
  item_key: string
  title: string
  published_version: number
  chunk_index: number
  score: number
  text: string
  retrieval_method?: string | null
  matched_terms?: string[]
  score_breakdown?: Record<string, number>
  direct_answer?: string | null
  answer_mode?: string | null
  source_metadata?: Record<string, unknown>
  metadata: Record<string, unknown>
}

export interface KnowledgeQueryAnalysis {
  language: string
  normalized_query: string
  entity_terms: string[]
  service_terms: string[]
  numeric_terms: string[]
  intent_terms: string[]
  terms: string[]
  high_value_terms: string[]
  fallback_ngrams: string[]
}

export interface KnowledgeRetrievalTestResult {
  hits: KnowledgeChunkHit[]
  total: number
  query_analysis?: KnowledgeQueryAnalysis | null
  candidate_count?: number
  top_hits?: Record<string, unknown>[]
  grounding_would_apply?: boolean
  grounding_source?: Record<string, unknown> | null
}

export interface KnowledgeConflictCheckResult {
  generated_at: string
  total: number
  conflicts: KnowledgeStudioConflict[]
  filters: Record<string, unknown>
}

export interface KnowledgeGoldenAssertion {
  key: string
  label: string
  passed: boolean
  expected?: string | null
  actual?: string | null
  evidence: string
}

export interface KnowledgeGoldenTestResult {
  generated_at: string
  passed: boolean
  query: string
  expected_item_key?: string | null
  assertions: KnowledgeGoldenAssertion[]
  retrieval: KnowledgeRetrievalTestResult
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

export interface ProviderCredentialStatus {
  id: string
  tenant_id: string
  provider: string
  provider_runtime: string
  credential_type: string
  profile_id: string
  account_id?: string | null
  email?: string | null
  chatgpt_plan_type?: string | null
  scope?: string | null
  expires_at?: string | null
  status: string
  last_used_at?: string | null
  last_refresh_at?: string | null
  last_error_code?: string | null
  token_fingerprint_prefix?: string | null
  created_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  revoked_at?: string | null
  secret_values_exposed: false
}

export interface ProviderCredentialStatusResponse {
  provider: string
  tenant_id: string
  credentials: ProviderCredentialStatus[]
  active_count: number
  secret_values_exposed: false
}

export interface CodexAuthorizationStart {
  session_id: string
  authorization_url: string
  expires_at: string
  scope?: string | null
  provider: string
}

export interface CodexManualAuthorizationStart extends CodexAuthorizationStart {
  state: string
  redirect_uri: string
}

export interface CodexManualAuthorizationCompleteRequest {
  session_id: string
  authorization_response: string
}

export interface CodexManualAuthorizationCompleteResult {
  ok: boolean
  status: string
  credential_id?: string | null
  provider: string
  elapsed_ms?: number
  secret_values_exposed: false
}

export interface CodexDeviceStart {
  session_id: string
  verification_url: string
  user_code: string
  expires_at: string
  interval: number
  scope?: string | null
}

export interface CodexSessionStatus {
  session_id?: string
  provider?: string
  flow_type?: string
  status: string
  error_code?: string | null
  expires_at?: string | null
  completed_at?: string | null
  scope?: string | null
  user_code?: string | null
  verification_url?: string | null
  credential_id?: string | null
}

export interface CodexCredentialActionResult {
  ok: boolean
  status: string
  credential_id?: string | null
  error_code?: string | null
  upstream_revoke?: string | null
}

export type WebchatMessageType = 'text' | 'system' | 'card' | 'action' | 'attachment' | 'voice_call'

export type WebchatCardType =
  | 'quick_replies'
  | 'tracking_status'
  | 'address_confirmation'
  | 'reschedule_picker'
  | 'photo_upload_request'
  | 'handoff'
  | 'csat'

export interface WebchatCardAction {
  id: string
  label: string
  value?: string | null
  action_type: string
  payload?: Record<string, unknown>
}

export interface WebchatCardPayload {
  card_id: string
  card_type: WebchatCardType
  version: number
  title: string
  body?: string | null
  actions: WebchatCardAction[]
  metadata?: Record<string, unknown>
}

export interface WebchatAIRuntimeSnapshot {
  ai_pending?: boolean
  ai_status?: string | null
  ai_turn_id?: number | null
  ai_pending_for_message_id?: number | null
  ai_suspended?: boolean
  handoff_status?: string | null
  current_handoff_request_id?: number | null
  active_agent_id?: number | null
  last_ai_reply_source?: string | null
  last_ai_fallback_reason?: string | null
  last_bridge_elapsed_ms?: number | null
}

export interface WebchatHandoffLastMessage {
  id: number
  direction: string
  body_text?: string | null
  message_type?: WebchatMessageType | null
  author_label?: string | null
  created_at?: string | null
}

export interface WebchatHandoffRequest extends WebchatAIRuntimeSnapshot {
  id: number | null
  conversation_id?: string | null
  webchat_conversation_id: number
  ticket_id: number
  ticket_no?: string | null
  title?: string | null
  status: string
  source: string
  trigger_type: string
  reason_code?: string | null
  reason_text?: string | null
  recommended_agent_action?: string | null
  assigned_agent_id?: number | null
  accepted_by_user_id?: number | null
  forced_by_user_id?: number | null
  declined_by_me?: boolean
  waiting_seconds?: number
  requested_at?: string | null
  accepted_at?: string | null
  released_at?: string | null
  closed_at?: string | null
  takeover_mode?: string | null
  visitor_name?: string | null
  visitor_email?: string | null
  visitor_phone?: string | null
  origin?: string | null
  last_message?: WebchatHandoffLastMessage | null
  can_accept?: boolean
  can_decline?: boolean
  can_force_takeover?: boolean
  can_release?: boolean
  can_resume_ai?: boolean
  can_reply?: boolean
  last_event_id?: number
  last_read_event_id?: number
  unread_count?: number
  marked_unread?: boolean
}

export interface WebchatHandoffQueue {
  items: WebchatHandoffRequest[]
  view: string
  permissions?: {
    can_accept?: boolean
    can_decline?: boolean
    can_force_takeover?: boolean
    can_release?: boolean
    can_resume_ai?: boolean
  }
}

export interface WebchatConversation extends WebchatAIRuntimeSnapshot {
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
  last_message_type?: WebchatMessageType | null
  last_action_status?: string | null
  needs_human?: boolean
  current_handoff_request_id?: number | null
  handoff_status?: string | null
  active_agent_id?: number | null
  ai_suspended?: boolean
  takeover_mode?: string | null
  last_handoff_reason?: string | null
  last_event_id?: number
  last_read_event_id?: number
  unread_count?: number
  marked_unread?: boolean
}

export interface WebchatMessage {
  id: number
  direction: 'visitor' | 'agent' | 'ai' | 'system' | 'action' | string
  body: string
  body_text?: string | null
  message_type?: WebchatMessageType
  payload_json?: WebchatCardPayload | Record<string, unknown> | null
  metadata_json?: Record<string, unknown> | null
  client_message_id?: string | null
  ai_turn_id?: number | null
  author_user_id?: number | null
  delivery_status?: string | null
  action_status?: string | null
  author_label?: string | null
  created_at?: string | null
}

export interface WebchatActionAudit {
  id: number
  message_id: number
  action_type: string
  status: string
  payload: Record<string, unknown>
  submitted_by: string
  origin?: string | null
  created_at?: string | null
}

export interface WebchatAITurnSummary {
  id: number
  status: string
  trigger_message_id?: number | null
  latest_visitor_message_id?: number | null
  context_cutoff_message_id?: number | null
  reply_message_id?: number | null
  reply_source?: string | null
  fallback_reason?: string | null
  bridge_elapsed_ms?: number | null
}

export interface WebchatEventSummary {
  id: number
  event_type: string
  payload_json?: Record<string, unknown> | null
  created_at?: string | null
}

export interface WebchatThread extends WebchatAIRuntimeSnapshot {
  conversation_id: string
  ticket_id: number
  ticket_no: string
  origin?: string | null
  page_url?: string | null
  status?: string | null
  conversation_state?: string | null
  required_action?: string | null
  handoff?: WebchatHandoffRequest | null
  visitor: {
    name?: string | null
    email?: string | null
    phone?: string | null
    ref?: string | null
  }
  messages: WebchatMessage[]
  actions?: WebchatActionAudit[]
  ai_turns?: WebchatAITurnSummary[]
  events?: WebchatEventSummary[]
  last_event_id?: number
  last_read_event_id?: number
  unread_count?: number
  marked_unread?: boolean
}

export interface WebchatReadStateResult {
  conversation_id: string
  ticket_id: number
  last_event_id: number
  last_read_event_id: number
  unread_count: number
  marked_unread: boolean
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
