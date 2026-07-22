export type VoiceRoutingMode = 'ai_first' | 'human_first'
export type VoiceRecordingPolicy = 'disabled' | 'consent_required' | 'always'
export type VoiceTranscriptionPolicy = 'disabled' | 'consent_required' | 'always'
export type VoiceOverflowAction = 'ai' | 'voicemail' | 'disconnect'

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
  voicemail_enabled: boolean
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
  voicemail_enabled: boolean
  recording_policy: VoiceRecordingPolicy
  transcription_policy: VoiceTranscriptionPolicy
  enabled: boolean
}

export interface VoiceSessionBootstrap {
  voice_session_id: string
  status: string
  provider: string
  livekit_url?: string | null
  participant_token?: string | null
  participant_identity?: string | null
  conversation_id?: string | null
  accepted_by_user_id?: number | null
  handoff_request_id?: number | null
}
