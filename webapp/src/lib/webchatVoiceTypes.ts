export type WebchatVoiceStatus = 'created' | 'ringing' | 'accepted' | 'active' | 'ended' | 'missed' | 'failed' | 'cancelled'

export interface WebchatVoiceSession {
  ok?: boolean
  voice_session_id: string
  status: WebchatVoiceStatus | string
  provider: string
  voice_page_url?: string | null
  room_name: string
  participant_token?: string | null
  expires_in_seconds?: number | null
  accepted_by_user_id?: number | null
  started_at?: string | null
  ringing_at?: string | null
  accepted_at?: string | null
  active_at?: string | null
  ended_at?: string | null
  expires_at?: string | null
}
