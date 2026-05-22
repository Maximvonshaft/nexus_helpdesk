export type WebchatVoiceStatus = 'created' | 'ringing' | 'accepted' | 'active' | 'ended' | 'missed' | 'failed' | 'cancelled'

export interface WebchatVoiceSession {
  ok?: boolean
  voice_session_id: string
  status: WebchatVoiceStatus | string
  provider: string
  voice_page_url?: string | null
  room_name: string
  provider_room_name?: string | null
  participant_identity?: string | null
  participant_token?: string | null
  expires_in_seconds?: number | null
  accepted_by_user_id?: number | null
  started_at?: string | null
  ringing_at?: string | null
  accepted_at?: string | null
  active_at?: string | null
  ended_at?: string | null
  expires_at?: string | null
  recording_status?: string | null
  transcript_status?: string | null
  summary_status?: string | null
}

export interface WebchatVoiceIncomingSession extends WebchatVoiceSession {
  ticket_id: number
  ticket_no?: string | null
  ticket_title?: string | null
  conversation_id?: string | null
  visitor_label?: string | null
  origin?: string | null
  page_url?: string | null
}

export interface WebchatVoiceRuntimeConfig {
  enabled: boolean
  provider: string
  livekit_url?: string | null
  recording_enabled: boolean
  transcription_enabled: boolean
}
