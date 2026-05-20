import { api } from '@/lib/api'

export class WebchatVoiceApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'WebchatVoiceApiError'
    this.status = status
  }
}

const voiceApi = {
  runtimeConfig: api.webchatVoiceRuntimeConfig,
  listSessions: api.webchatVoiceSessions,
  acceptSession: api.webchatVoiceAcceptSession,
  endSession: api.webchatVoiceEndSession,
}

export const webchatVoiceApi = voiceApi
