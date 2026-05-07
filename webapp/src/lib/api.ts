import type {
  AdminUser,
  AuthUser,
  BackgroundJob,
  Bulletin,
  CaseDetail,
  CaseListItem,
  ChannelAccount,
  ChannelOnboardingTaskList,
  LiteMeta,
  Market,
  ProductionReadiness,
  QueueSummary,
  RuntimeHealth,
  OpenClawConnectivityProbe,
  SignoffChecklist,
  AIConfigResource,
  AIConfigVersion,
  KnowledgeItemList,
  OpenClawUnresolvedEvent,
  PersonaProfileList,
  Team,
  WebchatConversation,
  WebchatThread,
  WebchatReplyResult,
} from '@/lib/types'

const STORAGE_KEY = 'helpdesk-webapp-token'
const PUBLIC_API_PATHS = ['/api/auth/login', '/auth/login', '/api/auth/register', '/auth/register', '/healthz', '/readyz']
const DEFAULT_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 12000)
const FRONTEND_LATENCY_DEBUG = String(import.meta.env.VITE_FRONTEND_LATENCY_DEBUG || '').toLowerCase() === 'true'

let authExpiryHandled = false

export class AuthExpiredError extends Error {
  constructor(message = '登录状态已失效，请重新登录') {
    super(message)
    this.name = 'AuthExpiredError'
  }
}

export class ApiTimeoutError extends Error {
  requestId: string

  constructor(requestId: string, timeoutMs: number) {
    super(`请求超时，请稍后重试。Request ID: ${requestId}; timeout: ${timeoutMs}ms`)
    this.name = 'ApiTimeoutError'
    this.requestId = requestId
  }
}

export function normalizeApiBaseUrl(raw: string | undefined | null) {
  const trimmed = (raw ?? '').trim().replace(/\/+$/, '')
  // The webapp API client owns the `/api/...` path prefix. Deployment envs must
  // provide only the origin/base host. Keep this defensive normalization so an
  // accidentally configured `VITE_API_BASE_URL=https://host/api` cannot generate
  // broken `/api/api/...` requests in production or Tailscale previews.
  return trimmed.replace(/\/api$/i, '')
}

const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL)

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

function requestMethod(init?: RequestInit) {
  return (init?.method || 'GET').toUpperCase()
}

function isSafeRetry(method: string) {
  return method === 'GET' || method === 'HEAD'
}

function createRequestId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function recordFrontendLatency(path: string, method: string, status: string, durationMs: number, requestId: string) {
  const safePath = requestPathname(path).replace(/\/\d+(?=\/|$)/g, '/{id}').replace(/\/[0-9a-fA-F]{8,}(?=\/|$)/g, '/{id}')
  const metric = {
    name: 'frontend_api_latency',
    method,
    path: safePath,
    status,
    duration_ms: Math.round(durationMs),
    request_id: requestId,
  }
  window.dispatchEvent(new CustomEvent('nexusdesk:frontend-api-latency', { detail: metric }))
  if (FRONTEND_LATENCY_DEBUG) console.debug('[frontend-api-latency]', metric)
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

export function getToken() {
  return sessionStorage.getItem(STORAGE_KEY)
}

export function setToken(token: string | null) {
  authExpiryHandled = false
  if (!token) sessionStorage.removeItem(STORAGE_KEY)
  else sessionStorage.setItem(STORAGE_KEY, token)
}

export function clearToken() {
  sessionStorage.removeItem(STORAGE_KEY)
}

async function performFetch(path: string, init: RequestInit | undefined, headers: Headers, timeoutMs: number, requestId: string) {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(buildApiUrl(path), { ...init, headers, signal: controller.signal })
  } catch (exc) {
    if (exc instanceof DOMException && exc.name === 'AbortError') {
      throw new ApiTimeoutError(requestId, timeoutMs)
    }
    throw exc
  } finally {
    window.clearTimeout(timeout)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const publicRequest = isPublicRequest(path)
  const token = getToken()
  const headers = new Headers(init?.headers ?? {})
  const method = requestMethod(init)
  const requestId = headers.get('X-Request-Id') || createRequestId()
  const timeoutMs = Number((init as RequestInit & { timeoutMs?: number })?.timeoutMs || DEFAULT_TIMEOUT_MS)
  const started = performance.now()
  headers.set('X-Request-Id', requestId)
  if (!(init?.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (token && !publicRequest) headers.set('Authorization', `Bearer ${token}`)

  let res: Response
  let attempt = 0
  while (true) {
    try {
      res = await performFetch(path, init, headers, timeoutMs, requestId)
      break
    } catch (exc) {
      const status = exc instanceof ApiTimeoutError ? 'timeout' : 'network_error'
      recordFrontendLatency(path, method, status, performance.now() - started, requestId)
      if (attempt === 0 && isSafeRetry(method)) {
        attempt += 1
        continue
      }
      throw exc
    }
  }

  const responseRequestId = res.headers.get('X-Request-Id') || requestId
  recordFrontendLatency(path, method, String(res.status), performance.now() - started, responseRequestId)
  if (res.status === 401) {
    if (publicRequest) {
      const msg = await readErrorMessage(res, '登录失败，请检查账号或密码')
      throw new Error(`${msg} Request ID: ${responseRequestId}`)
    }
    if (!authExpiryHandled) {
      authExpiryHandled = true
      clearToken()
    }
    throw new AuthExpiredError()
  }
  if (!res.ok) {
    const msg = await readErrorMessage(res, `${res.status} ${res.statusText}`)
    throw new Error(`${msg} Request ID: ${responseRequestId}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  login: (username: string, password: string) => request<{access_token: string; user: AuthUser}>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  me: () => request<AuthUser>('/api/auth/me'),

  adminUsers: () => request<AdminUser[]>('/api/admin/users'),
  createUser: (payload: Record<string, unknown>) => request<AdminUser>('/api/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateUser: (userId: number, payload: Record<string, unknown>) => request<AdminUser>(`/api/admin/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  activateUser: (userId: number) => request<AdminUser>(`/api/admin/users/${userId}/activate`, { method: 'POST' }),
  deactivateUser: (userId: number) => request<AdminUser>(`/api/admin/users/${userId}/deactivate`, { method: 'POST' }),
  resetUserPassword: (userId: number, password: string) => request<{ ok: boolean }>(`/api/admin/users/${userId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ password }),
  }),
  teams: () => request<Team[]>('/api/lookups/teams'),
  capabilityCatalog: () => request<string[]>('/api/admin/capabilities/catalog'),

  liteMeta: () => request<LiteMeta>('/api/lite/meta'),
  cases: (params?: { q?: string; status?: string }) => {
    const search = new URLSearchParams()
    if (params?.q) search.set('q', params.q)
    if (params?.status) search.set('status', params.status)
    return request<CaseListItem[]>(`/api/lite/cases${search.toString() ? `?${search.toString()}` : ''}`)
  },
  caseDetail: (ticketId: number) => request<CaseDetail>(`/api/tickets/${ticketId}`),
  workflowUpdate: (ticketId: number, payload: unknown) => request<CaseDetail>(`/api/lite/cases/${ticketId}/workflow-update`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  aiIntake: (ticketId: number, payload: unknown) => request<CaseDetail>(`/api/lite/cases/${ticketId}/ai-intake`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  markets: () => request<Market[]>('/api/lookups/markets'),
  bulletins: () => request<Bulletin[]>('/api/lookups/bulletins'),
  createBulletin: (payload: Partial<Bulletin>) => request<Bulletin>('/api/admin/bulletins', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateBulletin: (bulletinId: number, payload: Partial<Bulletin>) => request<Bulletin>(`/api/admin/bulletins/${bulletinId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  aiConfigs: (configType?: string) => request<AIConfigResource[]>(`/api/admin/ai-configs${configType ? `?config_type=${encodeURIComponent(configType)}` : ''}`),
  publishedAIConfigs: (configType?: string, marketId?: number) => {
    const search = new URLSearchParams()
    if (configType) search.set('config_type', configType)
    if (typeof marketId === 'number') search.set('market_id', String(marketId))
    return request<AIConfigResource[]>(`/api/lookups/ai-configs${search.toString() ? `?${search.toString()}` : ''}`)
  },
  createAIConfig: (payload: Partial<AIConfigResource>) => request<AIConfigResource>('/api/admin/ai-configs', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateAIConfig: (resourceId: number, payload: Partial<AIConfigResource>) => request<AIConfigResource>(`/api/admin/ai-configs/${resourceId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  publishAIConfig: (resourceId: number, notes?: string) => request<AIConfigVersion>(`/api/admin/ai-configs/${resourceId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  aiConfigVersions: (resourceId: number) => request<AIConfigVersion[]>(`/api/admin/ai-configs/${resourceId}/versions`),
  rollbackAIConfig: (resourceId: number, version: number, notes?: string) => request<AIConfigVersion>(`/api/admin/ai-configs/${resourceId}/rollback/${version}`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  personaProfiles: () => request<PersonaProfileList>('/api/persona-profiles?limit=200'),
  knowledgeItems: () => request<KnowledgeItemList>('/api/knowledge-items?limit=200'),
  channelOnboardingTasks: () => request<ChannelOnboardingTaskList>('/api/channel-control/onboarding-tasks?limit=200'),

  channelAccounts: () => request<ChannelAccount[]>('/api/admin/channel-accounts'),
  createChannelAccount: (payload: Partial<ChannelAccount>) => request<ChannelAccount>('/api/admin/channel-accounts', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateChannelAccount: (accountId: number, payload: Partial<ChannelAccount>) => request<ChannelAccount>(`/api/admin/channel-accounts/${accountId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),

  queueSummary: () => request<QueueSummary>('/api/admin/queues/summary'),
  runtimeHealth: () => request<RuntimeHealth>('/api/admin/openclaw/runtime-health'),
  openclawConnectivityCheck: () => request<OpenClawConnectivityProbe>('/api/admin/openclaw/connectivity-check'),
  readiness: () => request<ProductionReadiness>('/api/admin/production-readiness'),
  signoff: () => request<SignoffChecklist>('/api/admin/signoff-checklist'),
  jobs: () => request<BackgroundJob[]>('/api/admin/jobs?limit=50'),
  consumeOpenClawEventsOnce: () => request<{processed: number}>('/api/admin/openclaw/events/consume-once', { method: 'POST' }),

  webchatConversations: () => request<WebchatConversation[]>('/api/webchat/admin/conversations'),
  webchatThread: (ticketId: number) => request<WebchatThread>(`/api/webchat/admin/tickets/${ticketId}/thread`),
  webchatReply: (ticketId: number, payload: { body: string; has_fact_evidence?: boolean; confirm_review?: boolean }) => request<WebchatReplyResult>(`/api/webchat/admin/tickets/${ticketId}/reply`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  unresolvedEvents: () => request<OpenClawUnresolvedEvent[]>('/api/admin/openclaw/unresolved-events'),
  replayUnresolvedEvent: (eventId: number) => request<{ ok: boolean; linked_ticket_id?: number | null }>(`/api/admin/openclaw/unresolved-events/${eventId}/replay`, {
    method: 'POST',
  }),
  dropUnresolvedEvent: (eventId: number) => request<{ ok: boolean }>(`/api/admin/openclaw/unresolved-events/${eventId}/drop`, {
    method: 'POST',
  }),
}
