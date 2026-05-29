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
  ended_by_user_id?: number | null
  started_at?: string | null
  ringing_at?: string | null
  accepted_at?: string | null
  active_at?: string | null
  ended_at?: string | null
  expires_at?: string | null
  recording_status?: string | null
  transcript_status?: string | null
  summary_status?: string | null
  ringing_duration_seconds?: number | null
  talk_duration_seconds?: number | null
  total_duration_seconds?: number | null
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

export interface WebchatVoiceNoteResult {
  ok: boolean
  ticket_id: number
  voice_session_id: string
  note_id: number
  ticket_event_id: number
  webchat_event_id: number
  audit_id: number
  created_at: string
}

export interface WebchatVoiceTranscriptSegment {
  id: number
  segment_id: string
  speaker_type: string
  speaker_label?: string | null
  language?: string | null
  is_final: boolean
  start_ms?: number | null
  end_ms?: number | null
  text: string
  confidence?: number | null
  redaction_status: string
  created_at?: string | null
}

export interface WebchatVoiceAITurn {
  id: number
  turn_index: number
  customer_text_redacted?: string | null
  ai_response_text_redacted?: string | null
  language?: string | null
  intent?: string | null
  action?: string | null
  handoff_required: boolean
  handoff_reason?: string | null
  confidence?: number | null
  provider?: string | null
  stt_provider?: string | null
  tts_provider?: string | null
  latency_ms?: number | null
  created_at?: string | null
}

export interface WebchatVoiceAIAction {
  id: number
  turn_id?: number | null
  model_action: string
  nexus_decision: string
  decision_reason?: string | null
  speedaf_tool_name?: string | null
  background_job_id?: number | null
  tool_call_log_id?: number | null
  result_status?: string | null
  created_at?: string | null
}

export interface WebchatVoiceEvidence {
  ok: boolean
  ticket_id: number
  voice_session_id: string
  status: string
  provider: string
  recording_status?: string | null
  transcript_status?: string | null
  summary_status?: string | null
  ai_agent_status?: string | null
  ai_turn_count: number
  transcript_segments: WebchatVoiceTranscriptSegment[]
  ai_turns: WebchatVoiceAITurn[]
  ai_actions: WebchatVoiceAIAction[]
}
