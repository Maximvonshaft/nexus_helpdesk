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
export interface WhatsAppNativeAccountStatus {
  account_id: string
  status: string
  qr_status: string
  qr?: string | null
  qr_data_url?: string | null
  phone_number?: string | null
  jid?: string | null
  last_qr_generated_at?: string | null
  last_connected_at?: string | null
  last_disconnected_at?: string | null
  last_error_code?: string | null
  last_error_message?: string | null
  reconnect_count: number
  channel_account_id: number
  channel_health_status: string
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
  external_channel_transcript_count: number
  external_channel_attachment_references_count: number
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
  external_channel_transcript_count?: number
  external_channel_attachment_references_count?: number
  active_market_bulletins_count?: number
  external_channel_conversation?: {
    session_key: string
    channel?: string | null
    recipient?: string | null
    account_id?: string | null
    thread_id?: string | null
    last_synced_at?: string | null
  } | null
  external_channel_transcript?: TranscriptMessage[]
  attachments?: SystemAttachment[]
  external_channel_attachment_references?: AttachmentReference[]
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
  webchat_card_sent?: number
  webchat_handoff_ack_sent?: number
  pending_jobs: number
  dead_jobs: number
  external_channel_links: number
}
