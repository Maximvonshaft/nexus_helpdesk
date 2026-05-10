import { getToken } from '@/lib/api'
import type { WebchatVoiceSession } from '@/lib/webchatVoiceTypes'

function buildApiUrl(path: string) {
  const rawBase = (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/+$/, '').replace(/\/api$/i, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${rawBase}${normalizedPath}`
}

async function readErrorMessage(res: Response, fallback: string) {
  try {
    const data = await res.json()
    const detail = data?.detail
    return typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : JSON.stringify(data)
  } catch {
    return fallback
  }
}

async function adminRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const headers = new Headers(init?.headers ?? {})
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(buildApiUrl(path), { ...init, headers })
  if (!res.ok) {
    const msg = await readErrorMessage(res, `${res.status} ${res.statusText}`)
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export const webchatVoiceApi = {
  listSessions: (ticketId: number, init?: RequestInit) => adminRequest<{ items: WebchatVoiceSession[] }>(`/api/webchat/admin/tickets/${ticketId}/voice/sessions`, init),
  acceptSession: (ticketId: number, voiceSessionId: string) => adminRequest<WebchatVoiceSession>(`/api/webchat/admin/tickets/${ticketId}/voice/${voiceSessionId}/accept`, { method: 'POST' }),
  endSession: (ticketId: number, voiceSessionId: string) => adminRequest<{ ok: boolean; status: string; voice_session_id: string; accepted_by_user_id?: number | null }>(`/api/webchat/admin/tickets/${ticketId}/voice/${voiceSessionId}/end`, { method: 'POST' }),
}
