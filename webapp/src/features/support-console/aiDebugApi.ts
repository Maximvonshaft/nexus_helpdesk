import { ApiError, getSupportToken, normalizeSupportApiBaseUrl } from '@/lib/supportApi'

const API_BASE_URL = normalizeSupportApiBaseUrl(import.meta.env.VITE_API_BASE_URL)
const REQUEST_ID_HEADER = 'X-Request-Id'

function buildApiUrl(path: string) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}

function createRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

async function readErrorBody(res: Response, fallback: string) {
  try {
    const data = await res.json()
    const detail = data?.detail
    if (typeof detail === 'string' && detail.trim()) return { message: detail, detail, payload: data }
    if (detail && typeof detail === 'object' && typeof detail.message === 'string') return { message: detail.message, detail, payload: data }
    return { message: data?.message || fallback, detail, payload: data }
  } catch {
    return { message: fallback, detail: undefined, payload: undefined }
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {})
  const token = getSupportToken()
  headers.set('Content-Type', 'application/json')
  headers.set(REQUEST_ID_HEADER, headers.get(REQUEST_ID_HEADER) || createRequestId())
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(buildApiUrl(path), { ...init, headers, method: init?.method || 'GET' })
  if (!res.ok) {
    const { message, detail, payload } = await readErrorBody(res, `请求失败：${res.status}`)
    throw new ApiError(message, res.status, detail, payload)
  }
  if (res.status === 204) return undefined as T
  return await res.json() as T
}

export type AiDebugRun = {
  id: number
  conversation_id: number
  ticket_id: number
  ai_turn_id: number
  visitor_message_id?: number | null
  reply_message_id?: number | null
  request_id?: string | null
  channel?: string | null
  status: string
  intent?: string | null
  reply_type?: string | null
  reply_source?: string | null
  provider_status?: string | null
  tracking_intent_detected: boolean
  tracking_fact_evidence_present: boolean
  tool_facts_present: boolean
  live_tracking_answer_allowed: boolean
  kb_hits_count: number
  tool_call_count: number
  runtime_event_count: number
  safety_status?: string | null
  fact_gate_reason?: string | null
  customer_visible_message_created: boolean
  privacy?: Record<string, boolean>
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
}

export type AiDebugRunsResponse = {
  items: AiDebugRun[]
  total: number
  since_hours: number
}

export type AiDebugBundle = Record<string, any>

export type AiDebugFindingPayload = {
  finding_type: string
  severity: string
  tester_note?: string | null
  expected_behavior?: string | null
  actual_behavior?: string | null
}

export const aiDebugApi = {
  listDebugRuns: (params?: {
    since_hours?: number
    channel?: string
    intent?: string
    status?: string
    tracking_fact_evidence_present?: boolean
    live_tracking_answer_allowed?: boolean
    customer_visible_message_created?: boolean
    limit?: number
  }) => {
    const search = new URLSearchParams()
    search.set('since_hours', String(params?.since_hours ?? 24))
    search.set('limit', String(params?.limit ?? 50))
    if (params?.channel) search.set('channel', params.channel)
    if (params?.intent) search.set('intent', params.intent)
    if (params?.status) search.set('status', params.status)
    if (typeof params?.tracking_fact_evidence_present === 'boolean') search.set('tracking_fact_evidence_present', String(params.tracking_fact_evidence_present))
    if (typeof params?.live_tracking_answer_allowed === 'boolean') search.set('live_tracking_answer_allowed', String(params.live_tracking_answer_allowed))
    if (typeof params?.customer_visible_message_created === 'boolean') search.set('customer_visible_message_created', String(params.customer_visible_message_created))
    return request<AiDebugRunsResponse>(`/api/webchat/admin/debug-runs?${search.toString()}`)
  },
  getDebugBundle: (aiTurnId: number) => request<AiDebugBundle>(`/api/webchat/admin/ai-turns/${aiTurnId}/debug-bundle`),
  createFinding: (aiTurnId: number, payload: AiDebugFindingPayload) => request<Record<string, any>>(`/api/webchat/admin/ai-turns/${aiTurnId}/test-findings`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  createEvalCase: (findingId: number) => request<Record<string, any>>(`/api/webchat/admin/test-findings/${findingId}/eval-case`, { method: 'POST' }),
}
