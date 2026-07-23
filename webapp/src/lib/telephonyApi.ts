import { apiRequest } from '@/lib/apiClient'
import type {
  IncomingVoiceSessionList,
  VoiceCommandList,
  VoiceCommandResponse,
  VoiceEndResponse,
} from '@/lib/telephonyTypes'

export interface VoiceCommandRequest {
  action_type:
    | 'ai_suspend'
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
    | 'warm_transfer_complete'
    | 'warm_transfer_cancel'
    | 'recording_start'
    | 'recording_stop'
  target?: string | null
  digits?: string | null
  note?: string | null
  idempotency_key?: string | null
}

export const telephonyApi = {
  incomingOffers: (limit = 10) =>
    apiRequest<IncomingVoiceSessionList>(
      `/api/webchat/admin/voice/sessions?status=ringing&limit=${Math.max(1, Math.min(limit, 50))}`,
      { requestIdPrefix: 'incoming-voice' },
    ),

  rejectOffer: (voiceSessionId: string, reason = 'operator_declined_voice_offer') =>
    apiRequest(
      `/api/webchat/admin/voice/${encodeURIComponent(voiceSessionId)}/reject`,
      {
        method: 'POST',
        body: JSON.stringify({ reason }),
        requestIdPrefix: 'incoming-voice',
      },
    ),

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
