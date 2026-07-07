import type {
  AuthUser,
  ChannelAccount,
  KnowledgeItem,
  KnowledgeItemDetail,
  KnowledgeItemList,
  KnowledgeItemVersion,
  KnowledgeRetrievalTestResult,
  KnowledgeStudio,
  ProviderRuntimeStatus,
  SupportConversationDetail,
  SupportConversationMetrics,
  SupportConversationPage,
  SupportConversationReplyPayload,
  SupportConversationReplyResult,
  SupportConversationState,
  WebchatHandoffRequest,
  WhatsAppNativeAccountStatus,
} from '@/lib/types'
import type {
  SpeedafActionResponse,
  SpeedafAddressUpdatePayload,
  SpeedafCancelPayload,
  SpeedafCancelPreviewPayload,
  SpeedafCancelPreviewResponse,
  SpeedafWaybillLookupPayload,
  SpeedafWaybillLookupResponse,
  SpeedafWorkOrderPayload,
} from '@/lib/speedafTypes'

const STORAGE_KEY = 'helpdesk-webapp-token'
const REQUEST_ID_HEADER = 'X-Request-Id'
const DEFAULT_API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000)
const PUBLIC_API_PATHS = ['/api/auth/login', '/auth/login', '/healthz', '/readyz']

export class AuthExpiredError extends Error {
  constructor(message = '登录状态已失效，请重新登录') {
    super(message)
    this.name = 'AuthExpiredError'
  }
}

export class ApiError extends Error {
  status: number
  detail?: unknown
  payload?: unknown

  constructor(message: string, status: number, detail?: unknown, payload?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.payload = payload
  }
}

export function normalizeSupportApiBaseUrl(raw: string | undefined | null) {
  const trimmed = (raw ?? '').trim().replace(/\/+$/, '')
  return trimmed.replace(/\/api$/i, '')
}

const API_BASE_URL = normalizeSupportApiBaseUrl(import.meta.env.VITE_API_BASE_URL)

function buildApiUrl(path: string) {
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}

function requestPathname(path: string) {
  if (!/^https?:\/\//i.test(path)) return path.startsWith('/') ? path : `/${path}`
  try { return new URL(path).pathname } catch { return path }
}

function isPublicRequest(path: string) {
  const pathname = requestPathname(path)
  return PUBLIC_API_PATHS.some((publicPath) => pathname === publicPath || pathname.endsWith(publicPath))
}

function createRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export function getSupportToken() {
  return sessionStorage.getItem(STORAGE_KEY)
}

export function setSupportToken(token: string | null) {
  if (!token) sessionStorage.removeItem(STORAGE_KEY)
  else sessionStorage.setItem(STORAGE_KEY, token)
}

export function clearSupportToken() {
  sessionStorage.removeItem(STORAGE_KEY)
}

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number) {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), Math.max(timeoutMs, 1000))
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    window.clearTimeout(timeout)
  }
}

function errorMessage(status: number, detail: unknown, fallback: string) {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    const obj = detail as { message?: unknown; error?: unknown }
    if (typeof obj.message === 'string' && obj.message.trim()) return obj.message
    if (typeof obj.error === 'string' && obj.error.trim()) return obj.error
  }
  if (status === 401) return '登录状态已失效，请重新登录'
  return fallback
}

async function readErrorBody(res: Response, fallback: string) {
  try {
    const data = await res.json()
    const detail = data?.detail
    return {
      message: errorMessage(res.status, detail, data?.message || fallback),
      detail,
      payload: data,
    }
  } catch {
    return { message: fallback, detail: undefined, payload: undefined }
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const publicRequest = isPublicRequest(path)
  const headers = new Headers(init?.headers ?? {})
  const method = String(init?.method || 'GET').toUpperCase()
  const token = getSupportToken()

  if (!(init?.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  headers.set(REQUEST_ID_HEADER, headers.get(REQUEST_ID_HEADER) || createRequestId())
  if (token && !publicRequest) headers.set('Authorization', `Bearer ${token}`)

  let res: Response
  try {
    res = await fetchWithTimeout(buildApiUrl(path), { ...init, method, headers }, DEFAULT_API_TIMEOUT_MS)
  } catch (error) {
    const message = error instanceof DOMException && error.name === 'AbortError'
      ? '请求超时，请稍后重试'
      : '网络请求失败，请稍后重试'
    throw new ApiError(message, 0, undefined, undefined)
  }

  if (res.status === 401 && !publicRequest) {
    clearSupportToken()
    throw new AuthExpiredError()
  }
  if (!res.ok) {
    const { message, detail, payload } = await readErrorBody(res, `请求失败：${res.status}`)
    throw new ApiError(message, res.status, detail, payload)
  }
  if (res.status === 204) return undefined as T
  return await res.json() as T
}

export const supportApi = {
  login: (username: string, password: string) => request<{ access_token: string; user: AuthUser }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  me: () => request<AuthUser>('/api/auth/me'),

  supportConversations: (params?: { view?: string; channel?: string; q?: string; limit?: number }, init?: RequestInit) => {
    const search = new URLSearchParams()
    search.set('view', params?.view || 'open')
    search.set('channel', params?.channel || 'all')
    search.set('limit', String(params?.limit ?? 80))
    if (params?.q?.trim()) search.set('q', params.q.trim())
    return request<SupportConversationPage>(`/api/support/conversations?${search.toString()}`, init)
  },
  supportConversationDetail: (sessionKey: string, init?: RequestInit) => {
    const search = new URLSearchParams({ session_key: sessionKey })
    return request<SupportConversationDetail>(`/api/support/conversations/detail?${search.toString()}`, init)
  },
  supportConversationMetrics: (sinceHours = 24, init?: RequestInit) => request<SupportConversationMetrics>(`/api/support/conversations/metrics?since_hours=${sinceHours}`, init),
  supportConversationState: (init?: RequestInit) => request<SupportConversationState>('/api/support/conversations/state', init),
  supportConversationReply: (payload: SupportConversationReplyPayload) => request<SupportConversationReplyResult>('/api/support/conversations/reply', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  knowledgeStudio: () => request<KnowledgeStudio>('/api/lite/knowledge-studio'),
  knowledgeItems: (params?: { q?: string; status?: string; source_type?: string; knowledge_kind?: string; channel?: string; audience_scope?: string }) => {
    const search = new URLSearchParams()
    search.set('limit', '200')
    if (params?.q?.trim()) search.set('q', params.q.trim())
    if (params?.status?.trim()) search.set('status', params.status.trim())
    if (params?.source_type?.trim()) search.set('source_type', params.source_type.trim())
    if (params?.knowledge_kind?.trim()) search.set('knowledge_kind', params.knowledge_kind.trim())
    if (params?.channel?.trim()) search.set('channel', params.channel.trim())
    if (params?.audience_scope?.trim()) search.set('audience_scope', params.audience_scope.trim())
    return request<KnowledgeItemList>(`/api/knowledge-items?${search.toString()}`)
  },
  knowledgeItem: (itemId: number) => request<KnowledgeItemDetail>(`/api/knowledge-items/${itemId}`),
  createKnowledgeItem: (payload: Partial<KnowledgeItem>) => request<KnowledgeItem>('/api/knowledge-items', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateKnowledgeItem: (itemId: number, payload: Partial<KnowledgeItem>) => request<KnowledgeItem>(`/api/knowledge-items/${itemId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  publishKnowledgeItem: (itemId: number, notes?: string) => request<KnowledgeItemVersion>(`/api/knowledge-items/${itemId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  testKnowledgeRetrieval: (payload: { q: string; market_id?: number | null; channel?: string | null; audience_scope?: string | null; language?: string | null; limit?: number }) => request<KnowledgeRetrievalTestResult>('/api/knowledge-items/retrieve-test', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  channelAccounts: () => request<ChannelAccount[]>('/api/admin/channel-accounts'),
  whatsappNativeStatus: (accountId: string) => request<WhatsAppNativeAccountStatus>(`/api/admin/whatsapp/accounts/${encodeURIComponent(accountId)}/status`),
  providerRuntimeStatus: () => request<ProviderRuntimeStatus>('/api/admin/provider-runtime/status'),
  querySpeedafWaybills: (ticketId: number, payload: SpeedafWaybillLookupPayload) => request<SpeedafWaybillLookupResponse>(`/api/tickets/${ticketId}/speedaf/waybills/query`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  createSpeedafWorkOrder: (ticketId: number, payload: SpeedafWorkOrderPayload) => request<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/work-orders`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  submitSpeedafAddressUpdate: (ticketId: number, payload: SpeedafAddressUpdatePayload) => request<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/address-update`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  previewSpeedafCancel: (ticketId: number, payload: SpeedafCancelPreviewPayload) => request<SpeedafCancelPreviewResponse>(`/api/tickets/${ticketId}/speedaf/cancel-preview`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  confirmSpeedafCancel: (ticketId: number, payload: SpeedafCancelPayload) => request<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/cancel`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  webchatAcceptHandoff: (requestId: number, note?: string) => request<WebchatHandoffRequest>(`/api/webchat/admin/handoff/${requestId}/accept`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  }),
  webchatForceTakeover: (ticketId: number, payload?: { reason_code?: string; note?: string }) => request<WebchatHandoffRequest>(`/api/webchat/admin/tickets/${ticketId}/force-takeover`, {
    method: 'POST',
    body: JSON.stringify({ reason_code: payload?.reason_code || null, note: payload?.note || null }),
  }),
  webchatReleaseHandoff: (requestId: number, note?: string) => request<WebchatHandoffRequest>(`/api/webchat/admin/handoff/${requestId}/release`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  }),
  webchatResumeAi: (requestId: number, note?: string) => request<WebchatHandoffRequest>(`/api/webchat/admin/handoff/${requestId}/resume-ai`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  }),
}
