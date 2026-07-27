import { apiRequest } from '@/lib/apiClient'
import type {
  EmbeddedSignupCompleteResult,
  EmbeddedSignupCompletion,
  EmbeddedSignupIntent,
  EmbeddedSignupSession,
  WhatsAppBindingStatus,
  WhatsAppConnection,
  WhatsAppConnectionCreate,
  WhatsAppConnectionUpdate,
  WhatsAppPairingCode,
  WhatsAppTestResult,
} from '@/lib/whatsappTypes'

const base = '/api/admin/whatsapp/connections'
const embeddedSignupBase = '/api/admin/whatsapp/embedded-signup'

export const whatsappApi = {
  connections: () => apiRequest<WhatsAppConnection[]>(base),
  connection: (connectionId: number) => apiRequest<WhatsAppConnection>(`${base}/${connectionId}`),
  createConnection: (payload: WhatsAppConnectionCreate) => apiRequest<WhatsAppConnection>(base, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateConnection: (connectionId: number, payload: WhatsAppConnectionUpdate) => apiRequest<WhatsAppConnection>(`${base}/${connectionId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  startBinding: (connectionId: number) => apiRequest<WhatsAppBindingStatus>(`${base}/${connectionId}/binding/start`, {
    method: 'POST',
  }),
  bindingQr: (connectionId: number) => apiRequest<WhatsAppBindingStatus>(`${base}/${connectionId}/binding/qr`, {
    cache: 'no-store',
  }),
  requestPairingCode: (connectionId: number, phoneNumber: string) => apiRequest<WhatsAppPairingCode>(`${base}/${connectionId}/binding/pairing-code`, {
    method: 'POST',
    body: JSON.stringify({ phone_number: phoneNumber }),
  }),
  logout: (connectionId: number) => apiRequest<WhatsAppBindingStatus>(`${base}/${connectionId}/logout`, {
    method: 'POST',
  }),
  restart: (connectionId: number) => apiRequest<WhatsAppBindingStatus>(`${base}/${connectionId}/restart`, {
    method: 'POST',
  }),
  probe: (connectionId: number) => apiRequest<WhatsAppBindingStatus>(`${base}/${connectionId}/probe`, {
    method: 'POST',
  }),
  setDesiredState: (connectionId: number, desiredState: 'disabled' | 'active') => apiRequest<WhatsAppConnection>(`${base}/${connectionId}/desired-state`, {
    method: 'POST',
    body: JSON.stringify({ desired_state: desiredState }),
  }),
  subscribeMeta: (connectionId: number, callbackUrl?: string | null) => apiRequest<WhatsAppBindingStatus>(`${base}/${connectionId}/meta/subscribe`, {
    method: 'POST',
    body: JSON.stringify({ callback_url: callbackUrl || null }),
  }),
  testInbound: (connectionId: number, providerMessageId: string) => apiRequest<WhatsAppTestResult>(`${base}/${connectionId}/test-inbound`, {
    method: 'POST',
    body: JSON.stringify({ provider_message_id: providerMessageId }),
  }),
  testOutbound: (connectionId: number, target: string, body: string) => apiRequest<WhatsAppTestResult>(`${base}/${connectionId}/test-outbound`, {
    method: 'POST',
    body: JSON.stringify({ target, body }),
  }),
  createEmbeddedSignupSession: (payload: EmbeddedSignupIntent) => apiRequest<EmbeddedSignupSession>(`${embeddedSignupBase}/sessions`, {
    method: 'POST',
    body: JSON.stringify(payload),
    cache: 'no-store',
  }),
  completeEmbeddedSignup: (sessionId: string, payload: EmbeddedSignupCompletion) => apiRequest<EmbeddedSignupCompleteResult>(`${embeddedSignupBase}/sessions/${encodeURIComponent(sessionId)}/complete`, {
    method: 'POST',
    body: JSON.stringify(payload),
    cache: 'no-store',
  }),
}
