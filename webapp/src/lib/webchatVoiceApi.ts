import { api } from '@/lib/api'

export const webchatVoiceApi = {
  runtimeConfig: api.webchatVoiceRuntimeConfig,
  incomingSessions: api.webchatVoiceIncomingSessions,
  listSessions: api.webchatVoiceSessions,
  evidence: api.webchatVoiceEvidence,
  acceptSession: api.webchatVoiceAcceptSession,
  rejectSession: api.webchatVoiceRejectSession,
  endSession: api.webchatVoiceEndSession,
  saveNote: api.webchatVoiceSaveNote,
}
