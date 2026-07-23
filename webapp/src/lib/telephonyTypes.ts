export const INCOMING_VOICE_CONTEXT_PREFIX = 'nexus-incoming-voice-context:'

export type VoiceRoutingMode = 'ai_first' | 'human_first'
export type VoiceRecordingPolicy = 'disabled' | 'notice' | 'explicit_consent'
export type VoiceTranscriptionPolicy = 'disabled' | 'notice' | 'explicit_consent'
export type VoiceOverflowAction = 'ai' | 'disconnect'

export interface VoiceBusinessHourWindow {
  start: string
  end: string
}

export type VoiceBusinessHours = Partial<Record<
  'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday' | 'sunday',
  VoiceBusinessHourWindow[]
>>

export interface VoiceConfiguration {
  id?: number | null
  channel_account_id: number
  account_id: string
  phone_number?: string | null
  display_name?: string | null
  market_id?: number | null
  tenant_id?: number | null
  health_status?: string | null
  livekit_project_ref?: string | null
  inbound_trunk_id?: string | null
  outbound_trunk_id?: string | null
  dispatch_rule_id?: string | null
  routing_mode: VoiceRoutingMode
  ai_agent_name?: string | null
  timezone: string
  business_hours?: VoiceBusinessHours | null
  queue_timeout_seconds: number
  offer_timeout_seconds: number
  wrap_up_seconds: number
  overflow_action: VoiceOverflowAction
  recording_policy: VoiceRecordingPolicy
  transcription_policy: VoiceTranscriptionPolicy
  enabled: boolean
  updated_at?: string | null
}

export interface VoiceConfigurationUpdate {
  livekit_project_ref?: string | null
  inbound_trunk_id?: string | null
  outbound_trunk_id?: string | null
  dispatch_rule_id?: string | null
  routing_mode: VoiceRoutingMode
  ai_agent_name?: string | null
  timezone: string
  business_hours?: VoiceBusinessHours | null
  queue_timeout_seconds: number
  offer_timeout_seconds: number
  wrap_up_seconds: number
  overflow_action: VoiceOverflowAction
  recording_policy: VoiceRecordingPolicy
  transcription_policy: VoiceTranscriptionPolicy
  enabled: boolean
}

export interface VoiceCompliancePrompt {
  capability: 'recording' | 'transcript_persistence'
  policy: VoiceRecordingPolicy | VoiceTranscriptionPolicy
  policy_version: string
  prompt?: string | null
  prompt_sha256: string
  decision_required: boolean
}

export interface VoiceCompliancePolicyBundle {
  schema: 'nexus.voice-compliance-policy.v1'
  policy_version: string
  recording: VoiceCompliancePrompt
  transcript_persistence: VoiceCompliancePrompt
}

export type VoiceComplianceDecision = 'notice_delivered' | 'accepted' | 'declined' | 'timeout'

export interface VoiceComplianceEvidenceInput {
  capability: 'recording' | 'transcript_persistence'
  policy: VoiceRecordingPolicy | VoiceTranscriptionPolicy
  policy_version: string
  prompt_sha256: string
  decision: VoiceComplianceDecision
  idempotency_key: string
}

export interface VoiceOfferRead {
  id: string
  expires_at: string
}

export interface VoiceSessionBootstrap {
  voice_session_id: string
  status: string
  provider: string
  media_plane?: string | null
  livekit_url?: string | null
  participant_token?: string | null
  participant_identity?: string | null
  conversation_id?: string | null
  ticket_id?: number | null
  ticket_no?: string | null
  ticket_title?: string | null
  visitor_label?: string | null
  accepted_by_user_id?: number | null
  handoff_request_id?: number | null
  voice_offer?: VoiceOfferRead | null
  compliance?: VoiceCompliancePolicyBundle | null
}

export interface IncomingVoiceSession {
  ok: boolean
  voice_session_id: string
  status: string
  provider: string
  media_plane: 'livekit'
  voice_offer: VoiceOfferRead
  ticket_id?: number | null
  ticket_no?: string | null
  ticket_title?: string | null
  conversation_id?: string | null
  visitor_label?: string | null
  direction: string
  mode: string
  started_at?: string | null
  ringing_at?: string | null
  recording_status?: string | null
  transcript_status?: string | null
}

export interface IncomingVoiceSessionList {
  items: IncomingVoiceSession[]
}

export interface IncomingVoiceContext {
  voice_session_id: string
  conversation_id: string | null
  ticket_id: number | null
  ticket_no: string | null
  ticket_title: string | null
  visitor_label: string | null
}

export type VoiceCommandStatus =
  | 'requested'
  | 'dispatching'
  | 'retryable'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface VoiceCommandRead {
  id: string
  action_type: string
  status: VoiceCommandStatus
  provider_status: string
  provider_reason?: string | null
  provider_reference?: string | null
  idempotency_key: string
  attempt_count: number
  result: Record<string, unknown>
  actor_user_id?: number | null
  completed_at?: string | null
  next_attempt_at?: string | null
  created_at?: string | null
}

export interface VoiceCommandResponse {
  ok: boolean
  ticket_id?: number | null
  voice_session_id: string
  action: VoiceCommandRead
}

export interface VoiceCommandList {
  items: VoiceCommandRead[]
}

export interface VoiceEndResponse {
  ok: boolean
  status: string
  voice_session_id: string
  command?: VoiceCommandRead
}
