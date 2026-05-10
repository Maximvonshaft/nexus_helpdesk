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
const REQUEST_ID_HEADER = 'X-Request-Id'
const DEFAULT_API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000)
const PUBLIC_API_PATHS = ['/api/auth/login', '/auth/login', '/api/auth/register', '/auth/register', '/healthz', '/readyz']
const SAFE_RETRY_METHODS = new Set(['GET', 'HEAD'])

let authExpiryHandled = false

export class AuthExpiredError extends Error {
  constructor(message = '登录状态已失效，请重新登录') {
    super(message)
    this.name = 'AuthExpiredError'
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
  return String(init?.method || 'GET').toUpperCase()
}

function createRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function emitFrontendLatency(detail: { path: string; method: string; status: string; duration_ms: number; ok: boolean; timeout?: boolean }) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('nexusdesk:api-latency', { detail }))
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

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number) {
  const controller = new AbortController()
  let externalAbortHandler: (() => void) | undefined
  const timeout = window.setTimeout(() => controller.abort(), Math.max(timeoutMs, 1000))
  if (init.signal) {
    externalAbortHandler = () => controller.abort()
    init.signal.addEventListener('abort', externalAbortHandler, { once: true })
  }
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    window.clearTimeout(timeout)
    if (init.signal && externalAbortHandler) init.signal.removeEventListener('abort', externalAbortHandler)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const publicRequest = isPublicRequest(path)
  const token = getToken()
  const headers = new Headers(init?.headers ?? {})
  const method = requestMethod(init)
  const retryable = SAFE_RETRY_METHODS.has(method)
  const requestId = headers.get(REQUEST_ID_HEADER) || createRequestId()
  const apiPath = requestPathname(path)
  const url = buildApiUrl(path)
  if (!(init?.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  headers.set(REQUEST_ID_HEADER, requestId)
  if (token && !publicRequest) headers.set('Authorization', `Bearer ${token}`)

  let lastError: unknown
  for (let attempt = 0; attempt < (retryable ? 2 : 1); attempt += 1) {
    const started = performance.now()
    try {
      const res = await fetchWithTimeout(url, { ...init, method, headers }, DEFAULT_API_TIMEOUT_MS)
      const duration_ms = Math.round(performance.now() - started)
      emitFrontendLatency({ path: apiPath, method, status: String(res.status), duration_ms, ok: res.ok })
      if (res.status === 401) {
        if (publicRequest) {
          const msg = await readErrorMessage(res, '登录失败，请检查账号或密码')
          throw new Error(msg)
        }
        if (!authExpiryHandled) {
          authExpiryHandled = true
          clearToken()
        }
        throw new AuthExpiredError()
      }
      if (!res.ok) {
        const msg = await readErrorMessage(res, `${res.status} ${res.statusText}`)
        throw new Error(msg)
      }
      if (res.status === 204) return undefined as T
      return res.json() as Promise<T>
    } catch (error) {
      lastError = error
      const duration_ms = Math.round(performance.now() - started)
      const timeout = error instanceof DOMException && error.name === 'AbortError'
      emitFrontendLatency({ path: apiPath, method, status: timeout ? 'timeout' : 'network_error', duration_ms, ok: false, timeout })
      if (!retryable || attempt > 0 || error instanceof AuthExpiredError) break
    }
  }
  throw lastError instanceof Error ? lastError : new Error('API request failed')
}

export type TicketTimelinePage = {
  items: Array<Record<string, unknown>>
  next_cursor: string | null
  has_more: boolean
}

export const api = {
  login: (username: string, password: string) => request<{access_token: string; user: AuthUser}>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  me: () => request<AuthUser>('/api/auth/me'),

  adminUsers: () => request<AdminUser[]>('/api/admin/users?legacy=true'),
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
    search.set('legacy', 'true')
    if (params?.q) search.set('q', params.q)
    if (params?.status) search.set('status', params.status)
    return request<CaseListItem[]>(`/api/lite/cases?${search.toString()}`)
  },
  caseDetail: (ticketId: number) => request<CaseDetail>(`/api/tickets/${ticketId}/summary`),
  ticketTimeline: (ticketId: number, params?: { cursor?: string | null; limit?: number }) => {
    const search = new URLSearchParams()
    search.set('limit', String(params?.limit ?? 50))
    if (params?.cursor) search.set('cursor', params.cursor)
    return request<TicketTimelinePage>(`/api/tickets/${ticketId}/timeline?${search.toString()}`)
  },
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

  webchatConversations: (init?: RequestInit) => request<WebchatConversation[]>('/api/webchat/admin/conversations', init),
  webchatThread: (ticketId: number, init?: RequestInit) => request<WebchatThread>(`/api/webchat/admin/tickets/${ticketId}/thread`, init),
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
