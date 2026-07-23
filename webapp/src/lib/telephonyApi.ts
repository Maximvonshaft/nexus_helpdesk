import { apiRequest } from '@/lib/apiClient'
import type {
  VoiceCommandList,
  VoiceCommandResponse,
  VoiceEndResponse,
} from '@/lib/telephonyTypes'

export interface VoiceCommandRequest {
  action_type:
    | 'hangup'
    | 'hold'
    | 'resume'
    | 'mute'
    | 'unmute'
    | 'keypad'
    | 'add_participant'
    | 'remove_participant'
    | 'cold_transfer'
    | 'warm_transfer'
    | 'recording_start'
    | 'recording_stop'
  target?: string | null
  digits?: string | null
  note?: string | null
  idempotency_key?: string | null
}

export const telephonyApi = {
  recordCommand: (voiceSessionId: string, payload: VoiceCommandRequest) =>
    apiRequest<VoiceCommandResponse>(
      `/api/webchat/admin/voice/${encodeURIComponent(voiceSessionId)}/actions`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    ),

  listCommands: (voiceSessionId: string, limit = 50) =>
    apiRequest<VoiceCommandList>(
      `/api/webchat/admin/voice/${encodeURIComponent(voiceSessionId)}/actions?limit=${Math.max(1, Math.min(limit, 100))}`,
    ),

  endSession: (voiceSessionId: string) =>
    apiRequest<VoiceEndResponse>(
      `/api/webchat/admin/voice/${encodeURIComponent(voiceSessionId)}/end`,
      { method: 'POST' },
    ),
}
