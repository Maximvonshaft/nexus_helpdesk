import { api } from '@/lib/api'

const voiceApi = {
  runtimeConfig: api.webchatVoiceRuntimeConfig,
  listSessions: api.webchatVoiceSessions,
  acceptSession: api.webchatVoiceAcceptSession,
  endSession: api.webchatVoiceEndSession,
}

export const webchatVoiceApi = voiceApi
