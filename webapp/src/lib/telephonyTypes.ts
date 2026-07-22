export type VoiceRoutingMode = 'ai_first' | 'human_first'
export type VoiceRecordingPolicy = 'disabled' | 'consent_required'

export interface VoiceConfiguration {
  id: number
  channel_account_id: number
  account_id: string
  display_name?: string | null
  market_id?: number | null
  tenant_id?: number | null
  inbound_trunk_id?: string | null
  outbound_trunk_id?: string | null
  routing_mode: VoiceRoutingMode
  ai_agent_name?: string | null
  queue_timeout_seconds: number
  wrap_up_seconds: number
  recording_policy: VoiceRecordingPolicy
  enabled: boolean
  updated_at?: string | null
}

export interface VoiceConfigurationUpdate {
  inbound_trunk_id?: string | null
  outbound_trunk_id?: string | null
  routing_mode: VoiceRoutingMode
  ai_agent_name?: string | null
  queue_timeout_seconds: number
  wrap_up_seconds: number
  recording_policy: VoiceRecordingPolicy
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
