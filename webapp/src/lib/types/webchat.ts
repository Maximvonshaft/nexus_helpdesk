import type { BadgeTone } from './core'

export type WebchatMessageType = 'text' | 'system' | 'card' | 'action' | 'attachment' | 'voice_call'
export type WebchatCardType =
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
  ai_status_elapsed_ms?: number | null
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
export interface SupportMemoryTimelineItem {
  kind: string
  label?: string | null
  status?: string | null
  summary?: Record<string, unknown>
  created_at?: string | null
  source_id?: string | null
}
export interface SupportMemoryNextAction {
  key: string
  label: string
  tone?: BadgeTone | string
}
export interface SupportMemoryLedger {
  generated_at?: string | null
  source: 'derived_support_memory_ledger' | string
  ticket: {
    id: number
    ticket_no?: string | null
    status?: string | null
    conversation_state?: string | null
    source_channel?: string | null
    market_code?: string | null
    country_code?: string | null
  }
  conversation: {
    id: string
    status?: string | null
    channel_key?: string | null
    origin?: string | null
    last_seen_at?: string | null
    updated_at?: string | null
  }
  current_intent?: string | null
  customer_request?: string | null
  required_action?: string | null
  missing_fields: string[]
  tracking: {
    present: boolean
    suffix?: string | null
    hash?: string | null
    source?: string | null
    raw_exposed?: boolean
  }
  ai_state: WebchatAIRuntimeSnapshot & {
    last_turn?: SupportMemoryTimelineItem | null
  }
  handoff?: WebchatHandoffRequest | null
  latest_speedaf_evidence?: SupportMemoryTimelineItem | null
  evidence_summary: Record<string, number>
  evidence_timeline: SupportMemoryTimelineItem[]
  next_actions: SupportMemoryNextAction[]
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
  support_memory?: SupportMemoryLedger
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
export interface SupportConversationReplyPayload {
  session_key: string
  body: string
}
export interface SupportConversationReplyResult extends WebchatReplyResult {
  session_key: string
  channel: SupportConversationChannel
  message_id: number
  outbound_message_id?: number | null
}
export type SupportConversationChannel = 'webchat' | 'whatsapp' | string
export interface SupportConversation {
  session_key: string
  conversation_id: string
  channel: SupportConversationChannel
  source?: string | null
  ticket_id: number
  ticket_no: string
  title: string
  status: string
  conversation_state: string
  display_name: string
  customer_contact?: string | null
  updated_at?: string | null
  last_seen_at?: string | null
  latest_message?: string | null
  latest_author?: 'customer' | 'agent' | 'ai' | 'system' | string | null
  needs_human: boolean
  required_action?: string | null
  handoff_status?: string | null
  handoff_request_id?: number | null
  active_agent_id?: number | null
  ai_pending?: boolean
  ai_status?: string | null
  ai_turn_id?: number | null
  ai_pending_for_message_id?: number | null
  ai_status_elapsed_ms?: number | null
  ai_suspended?: boolean
  tracking_number_present?: boolean
  tracking_number?: string | null
  tracking_reference?: string | null
  pii_minimized?: boolean
  can_force_takeover?: boolean
  can_accept?: boolean
  can_release?: boolean
  can_resume_ai?: boolean
  can_reply?: boolean
}
export interface SupportConversationPage {
  items: SupportConversation[]
  source: string
  view: string
}
export interface SupportConversationMetrics {
  source: string
  since_hours: number
  total: number
  needs_human: number
  ai_active: number
  by_channel: Record<string, number>
  by_state: Record<string, number>
  runtime_latency?: RuntimeLatencySummary
}
export interface RuntimeLatencyBucket {
  count: number
  p50_ms?: number | null
  p90_ms?: number | null
  max_ms?: number | null
}
export interface RuntimeLatencySummary {
  sample_count: number
  failed_count: number
  cold_load_count: number
  slow_prompt_eval_count: number
  by_latency_class: Record<string, number>
  total_turn: RuntimeLatencyBucket
  bridge: RuntimeLatencyBucket
  runtime_total: RuntimeLatencyBucket
  runtime_load: RuntimeLatencyBucket
  runtime_prompt_eval: RuntimeLatencyBucket
  runtime_eval: RuntimeLatencyBucket
}
export interface SupportConversationState {
  source: string
  open: number
  requested_handoffs: number
  my_handoffs: number
  generated_at?: string | null
}
