import { ApiError, getSupportToken, normalizeSupportApiBaseUrl } from '@/lib/supportApi'

const API_BASE_URL = normalizeSupportApiBaseUrl(import.meta.env.VITE_API_BASE_URL)

function buildApiUrl(path: string) {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`
}

function createRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `runtime-audit-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {})
  const token = getSupportToken()
  headers.set('Content-Type', 'application/json')
  headers.set('X-Request-Id', headers.get('X-Request-Id') || createRequestId())
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(buildApiUrl(path), { ...init, headers, method: init?.method || 'GET' })
  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json())?.detail
    } catch {
      detail = undefined
    }
    const message = typeof detail === 'string' && detail.trim() ? detail : `请求失败：${response.status}`
    throw new ApiError(message, response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return await response.json() as T
}

export type AiDebugRun = {
  id: number
  conversation_id: number
  ticket_id: number
  ai_turn_id: number
  channel?: string | null
  status: string
  intent?: string | null
  tracking_intent_detected: boolean
  tracking_fact_evidence_present: boolean
  live_tracking_answer_allowed: boolean
  kb_hits_count: number
  tool_call_count: number
  customer_visible_message_created: boolean
  created_at?: string | null
}

export type AiDebugBundle = Record<string, any>

export const aiDebugApi = {
  listDebugRuns: (params: {
    since_hours: number
    channel?: string
    tracking_fact_evidence_present?: boolean
    live_tracking_answer_allowed?: boolean
    customer_visible_message_created?: boolean
    limit: number
  }) => {
    const search = new URLSearchParams({ since_hours: String(params.since_hours), limit: String(params.limit) })
    if (params.channel) search.set('channel', params.channel)
    if (typeof params.tracking_fact_evidence_present === 'boolean') search.set('tracking_fact_evidence_present', String(params.tracking_fact_evidence_present))
    if (typeof params.live_tracking_answer_allowed === 'boolean') search.set('live_tracking_answer_allowed', String(params.live_tracking_answer_allowed))
    if (typeof params.customer_visible_message_created === 'boolean') search.set('customer_visible_message_created', String(params.customer_visible_message_created))
    return request<{ items: AiDebugRun[]; total: number }>(`/api/webchat/admin/debug-runs?${search.toString()}`)
  },
  getDebugBundle: (aiTurnId: number) => request<AiDebugBundle>(`/api/webchat/admin/ai-turns/${aiTurnId}/debug-bundle`),
  createFinding: (aiTurnId: number, payload: Record<string, unknown>) => request<Record<string, any>>(`/api/webchat/admin/ai-turns/${aiTurnId}/test-findings`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  createEvalCase: (findingId: number) => request<Record<string, any>>(`/api/webchat/admin/test-findings/${findingId}/eval-case`, { method: 'POST' }),
}
