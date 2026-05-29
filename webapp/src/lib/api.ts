import type {
  AdminUser,
  AuthUser,
  BackgroundJob,
  Bulletin,
  CaseDetail,
  CaseListItem,
  CaseListPage,
  ChannelAccount,
  ChannelOnboardingTaskList,
  LiteMeta,
  Market,
  ProductionReadiness,
  QueueSummary,
  RuntimeHealth,
  OpenClawConnectivityProbe,
  OutboundChannelCapabilitiesResponse,
  OutboundEmailAccount,
  OutboundEmailAccountCreate,
  OutboundEmailAccountUpdate,
  OutboundEmailTestSendRequest,
  OutboundEmailTestSendResult,
  OutboundReplyTemplate,
  OutboundSendPayload,
  SignoffChecklist,
  SystemAttachment,
  AIConfigResource,
  AIConfigVersion,
  KnowledgeItem,
  KnowledgeItemDetail,
  KnowledgeItemList,
  KnowledgeItemVersion,
  KnowledgeRetrievalTestResult,
  OpenClawUnresolvedEvent,
  PersonaProfile,
  PersonaProfileDetail,
  PersonaProfileList,
  PersonaProfileVersion,
  Team,
  WebchatConversation,
  WebchatHandoffQueue,
  WebchatHandoffRequest,
  WebchatReadStateResult,
  WebchatThread,
  WebchatReplyResult,
  ProviderCredentialStatusResponse,
  CodexAuthorizationStart,
  CodexManualAuthorizationCompleteResult,
  CodexManualAuthorizationStart,
  CodexDeviceStart,
  CodexSessionStatus,
  CodexCredentialActionResult,
} from '@/lib/types'
import type { WebchatVoiceIncomingSession, WebchatVoiceRuntimeConfig, WebchatVoiceSession } from '@/lib/webchatVoiceTypes'
import { mapApiErrorMessage } from '@/lib/apiErrorMap'

const STORAGE_KEY = 'helpdesk-webapp-token'
const REQUEST_ID_HEADER = 'X-Request-Id'
const DEFAULT_API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000)
const PUBLIC_API_PATHS = ['/api/auth/login', '/auth/login', '/api/auth/register', '/auth/register', '/healthz', '/readyz', '/api/webchat/voice/runtime-config']
const SAFE_RETRY_METHODS = new Set(['GET', 'HEAD'])

let authExpiryHandled = false

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

async function readErrorBody(res: Response, fallback: string) {
  try {
    const data = await res.json()
    const detail = data?.detail
    return {
      message: mapApiErrorMessage(res.status, detail, JSON.stringify(data) || fallback),
      detail,
      payload: data,
    }
  } catch {
    return { message: fallback, detail: undefined, payload: undefined }
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
          const err = await readErrorBody(res, '登录失败，请检查账号或密码')
          throw new ApiError(err.message, res.status, err.detail, err.payload)
        }
        if (!authExpiryHandled) {
          authExpiryHandled = true
          clearToken()
        }
        throw new AuthExpiredError()
      }
      if (!res.ok) {
        const err = await readErrorBody(res, `${res.status} ${res.statusText}`)
        throw new ApiError(err.message, res.status, err.detail, err.payload)
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

export type WebchatEventsPage = {
  events: { id: number; event_type: string }[]
  last_event_id: number
}

export type RuntimeRecoveryResult = {
  ok: boolean
  requeued?: number
  job_id?: number
  message_id?: number
  status?: string
  job_type?: string | null
}

export type WebCallAIDemoStatus = {
  ok: boolean
  status: 'disabled' | 'ready' | 'degraded' | 'blocked'
  enabled: boolean
  kill_switch: boolean
  internal_only: boolean
  public_customer_entry_enabled: boolean
  recording_enabled: boolean
  transcription_enabled: boolean
  ai_agent_enabled: boolean
  demo_mode: string
  allow_browser_speech: boolean
  allow_real_media: boolean
  active_demo_sessions: number
  max_active_sessions: number
  max_turns_per_session: number
  blockers: string[]
  warnings: string[]
}

export type WebCallAIDemoSession = {
  public_id: string
  mode: string
  status: string
  locale?: string | null
  recording_status?: string | null
  transcript_status?: string | null
  summary_status?: string | null
  ai_agent_status?: string | null
  ai_turn_count: number
  created_at?: string | null
  ended_at?: string | null
}

export type WebCallAIDemoEvent = { id?: number | string; type: string; summary?: string; created_at?: string | null }
export type WebCallAIDemoTurn = {
  id: number
  turn_index: number
  status: string
  customer_text_redacted: string
  ai_response_text_redacted: string
  language?: string | null
  intent?: string | null
  action?: string | null
  handoff_required: boolean
  confidence?: number | null
  tts_mode?: string | null
  created_at?: string | null
}

type CaseQueryParams = { q?: string; status?: string; priority?: string; assignee_id?: number; team_id?: number; overdue?: boolean; cursor?: string | null; limit?: number }

function buildCaseSearch(params?: CaseQueryParams) {
  const search = new URLSearchParams()
  search.set('limit', String(params?.limit ?? 50))
  if (params?.q) search.set('q', params.q)
  if (params?.status) search.set('status', params.status)
  if (params?.priority) search.set('priority', params.priority)
  if (typeof params?.assignee_id === 'number') search.set('assignee_id', String(params.assignee_id))
  if (typeof params?.team_id === 'number') search.set('team_id', String(params.team_id))
  if (typeof params?.overdue === 'boolean') search.set('overdue', String(params.overdue))
  if (params?.cursor) search.set('cursor', params.cursor)
  return search
}

function buildWebchatEventsSearch(afterId: number, limit = 50, waitMs = 1500) {
  const search = new URLSearchParams()
  search.set('after_id', String(afterId))
  search.set('limit', String(limit))
  search.set('wait_ms', String(waitMs))
  return search
}

function buildRecoverySearch(params?: { job_type?: string; limit?: number }) {
  const search = new URLSearchParams()
  if (params?.job_type) search.set('job_type', params.job_type)
  if (typeof params?.limit === 'number') search.set('limit', String(params.limit))
  return search.toString()
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
  casesPage: (params?: CaseQueryParams) => request<CaseListPage>(`/api/lite/cases?${buildCaseSearch(params).toString()}`),
  cases: async (params?: CaseQueryParams): Promise<CaseListItem[]> => {
    const page = await request<CaseListPage>(`/api/lite/cases?${buildCaseSearch(params).toString()}`)
    return page.items
  },
  caseDetail: (ticketId: number) => request<CaseDetail>(`/api/tickets/${ticketId}/summary`),
  ticketTimeline: (ticketId: number, params?: { cursor?: string | null; limit?: number }) => {
    const search = new URLSearchParams()
    search.set('limit', String(params?.limit ?? 50))
    if (params?.cursor) search.set('cursor', params.cursor)
    return request<TicketTimelinePage>(`/api/tickets/${ticketId}/timeline?${search.toString()}`)
  },
  ticketOutboundChannelCapabilities: (ticketId: number) => request<OutboundChannelCapabilitiesResponse>(`/api/tickets/${ticketId}/outbound/channels/capabilities`),
  ticketOutboundReplyTemplates: (ticketId: number, channel = 'email') => {
    const search = new URLSearchParams()
    search.set('channel', channel)
    return request<OutboundReplyTemplate[]>(`/api/tickets/${ticketId}/outbound/templates?${search.toString()}`)
  },
  uploadTicketAttachment: (ticketId: number, file: File, visibility = 'external') => {
    const form = new FormData()
    form.set('file', file)
    form.set('visibility', visibility)
    return request<SystemAttachment>(`/api/tickets/${ticketId}/attachments`, {
      method: 'POST',
      body: form,
    })
  },
  escalateTicket: (ticketId: number, payload: { team_id: number; note: string }) => request<Record<string, unknown>>(`/api/tickets/${ticketId}/escalate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  saveOutboundDraft: (ticketId: number, payload: OutboundSendPayload) => request<Record<string, unknown>>(`/api/tickets/${ticketId}/outbound/draft`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  sendOutboundMessage: (ticketId: number, payload: OutboundSendPayload) => request<Record<string, unknown>>(`/api/tickets/${ticketId}/outbound/send`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
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
  personaProfiles: (params?: { q?: string; channel?: string; language?: string; is_active?: boolean }) => {
    const search = new URLSearchParams()
    search.set('limit', '200')
    if (params?.q) search.set('q', params.q)
    if (params?.channel) search.set('channel', params.channel)
    if (params?.language) search.set('language', params.language)
    if (typeof params?.is_active === 'boolean') search.set('is_active', String(params.is_active))
    return request<PersonaProfileList>(`/api/persona-profiles?${search.toString()}`)
  },
  personaProfile: (profileId: number) => request<PersonaProfileDetail>(`/api/persona-profiles/${profileId}`),
  createPersonaProfile: (payload: Partial<PersonaProfile>) => request<PersonaProfile>('/api/persona-profiles', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updatePersonaProfile: (profileId: number, payload: Partial<PersonaProfile>) => request<PersonaProfile>(`/api/persona-profiles/${profileId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  publishPersonaProfile: (profileId: number, notes?: string) => request<PersonaProfileVersion>(`/api/persona-profiles/${profileId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  rollbackPersonaProfile: (profileId: number, version: number, notes?: string) => request<PersonaProfileVersion>(`/api/persona-profiles/${profileId}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ version, notes: notes || null }),
  }),
  knowledgeItems: (params?: { q?: string; status?: string; source_type?: string; channel?: string; audience_scope?: string }) => {
    const search = new URLSearchParams()
    search.set('limit', '200')
    if (params?.q) search.set('q', params.q)
    if (params?.status) search.set('status', params.status)
    if (params?.source_type) search.set('source_type', params.source_type)
    if (params?.channel) search.set('channel', params.channel)
    if (params?.audience_scope) search.set('audience_scope', params.audience_scope)
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
  uploadKnowledgeDocument: (itemId: number, file: File) => {
    const form = new FormData()
    form.set('file', file)
    return request<KnowledgeItem>(`/api/knowledge-items/${itemId}/upload`, {
      method: 'POST',
      body: form,
    })
  },
  createKnowledgeItemFromUpload: (file: File, params?: { item_key?: string; title?: string; market_id?: number; channel?: string; audience_scope?: string; language?: string }) => {
    const form = new FormData()
    form.set('file', file)
    if (params?.item_key) form.set('item_key', params.item_key)
    if (params?.title) form.set('title', params.title)
    if (params?.market_id) form.set('market_id', String(params.market_id))
    if (params?.channel) form.set('channel', params.channel)
    if (params?.audience_scope) form.set('audience_scope', params.audience_scope)
    if (params?.language) form.set('language', params.language)
    return request<KnowledgeItem>('/api/knowledge-items/upload', {
      method: 'POST',
      body: form,
    })
  },
  publishKnowledgeItem: (itemId: number, notes?: string) => request<KnowledgeItemVersion>(`/api/knowledge-items/${itemId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  rollbackKnowledgeItem: (itemId: number, version: number, notes?: string) => request<KnowledgeItemVersion>(`/api/knowledge-items/${itemId}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ version, notes: notes || null }),
  }),
  testKnowledgeRetrieval: (payload: { q: string; market_id?: number | null; channel?: string | null; audience_scope?: string | null; language?: string | null; limit?: number }) => request<KnowledgeRetrievalTestResult>('/api/knowledge-items/retrieve-test', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
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
  outboundEmailAccounts: () => request<OutboundEmailAccount[]>('/api/admin/outbound-email/accounts'),
  outboundEmailAccount: (accountId: number) => request<OutboundEmailAccount>(`/api/admin/outbound-email/accounts/${accountId}`),
  createOutboundEmailAccount: (payload: OutboundEmailAccountCreate) => request<OutboundEmailAccount>('/api/admin/outbound-email/accounts', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateOutboundEmailAccount: (accountId: number, payload: OutboundEmailAccountUpdate) => request<OutboundEmailAccount>(`/api/admin/outbound-email/accounts/${accountId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  enableOutboundEmailAccount: (accountId: number) => request<OutboundEmailAccount>(`/api/admin/outbound-email/accounts/${accountId}/enable`, { method: 'POST' }),
  disableOutboundEmailAccount: (accountId: number) => request<OutboundEmailAccount>(`/api/admin/outbound-email/accounts/${accountId}/disable`, { method: 'POST' }),
  testOutboundEmailAccount: (accountId: number, payload: OutboundEmailTestSendRequest) => request<OutboundEmailTestSendResult>(`/api/admin/outbound-email/accounts/${accountId}/test-send`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  queueSummary: () => request<QueueSummary>('/api/admin/queues/summary'),
  runtimeHealth: () => request<RuntimeHealth>('/api/admin/openclaw/runtime-health'),
  openclawConnectivityCheck: () => request<OpenClawConnectivityProbe>('/api/admin/openclaw/connectivity-check'),
  readiness: () => request<ProductionReadiness>('/api/admin/production-readiness'),
  signoff: () => request<SignoffChecklist>('/api/admin/signoff-checklist'),
  jobs: () => request<BackgroundJob[]>('/api/admin/jobs?limit=50'),
  requeueJob: (jobId: number) => request<RuntimeRecoveryResult>(`/api/admin/jobs/${jobId}/requeue`, { method: 'POST' }),
  requeueDeadJobs: (params?: { job_type?: string; limit?: number }) => {
    const search = buildRecoverySearch(params)
    return request<RuntimeRecoveryResult>(`/api/admin/jobs/requeue-dead${search ? `?${search}` : ''}`, { method: 'POST' })
  },
  requeueOutboundMessage: (messageId: number) => request<RuntimeRecoveryResult>(`/api/admin/outbound/${messageId}/requeue`, { method: 'POST' }),
  requeueDeadOutbound: (params?: { limit?: number }) => {
    const search = buildRecoverySearch(params)
    return request<RuntimeRecoveryResult>(`/api/admin/outbound/requeue-dead${search ? `?${search}` : ''}`, { method: 'POST' })
  },
  consumeOpenClawEventsOnce: () => request<{processed: number}>('/api/admin/openclaw/events/consume-once', { method: 'POST' }),

  webcallAIDemoStatus: () => request<WebCallAIDemoStatus>('/api/admin/webcall-ai-demo/status'),
  webcallAIDemoCreateSession: (payload: { locale?: string; display_name?: string; scenario?: string; initial_text?: string }) => request<{ ok: boolean; session: WebCallAIDemoSession; events: WebCallAIDemoEvent[] }>('/api/admin/webcall-ai-demo/sessions', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  webcallAIDemoTurn: (sessionId: string, payload: { client_turn_id: string; input_mode: string; locale?: string; text: string; browser_speech_supported?: boolean }) => request<{ ok: boolean; voice_session_public_id: string; turn: WebCallAIDemoTurn; events: WebCallAIDemoEvent[]; evidence: { voice_ai_turn_id: number; transcript_segment_id: number; tool_call_log_id: number | null } }>(`/api/admin/webcall-ai-demo/sessions/${sessionId}/turns`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  webcallAIDemoEndSession: (sessionId: string, reason = 'operator_end') => request<{ ok: boolean; session: WebCallAIDemoSession }>(`/api/admin/webcall-ai-demo/sessions/${sessionId}/end`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  }),
  webcallAIDemoEvents: (sessionId: string) => request<{ ok: boolean; session: Pick<WebCallAIDemoSession, 'public_id' | 'status' | 'mode'>; events: WebCallAIDemoEvent[]; turns: Array<Pick<WebCallAIDemoTurn, 'turn_index' | 'customer_text_redacted' | 'ai_response_text_redacted' | 'handoff_required' | 'created_at'> & { turn_id: number }> }>(`/api/admin/webcall-ai-demo/sessions/${sessionId}/events`),

  codexCredentialStatus: () => request<ProviderCredentialStatusResponse>('/api/admin/provider-credentials/codex/status'),
  startCodexAuthorization: (scopes?: string[]) => request<CodexAuthorizationStart>('/api/admin/provider-credentials/codex/authorize', {
    method: 'POST',
    body: JSON.stringify({ scopes: scopes?.length ? scopes : null }),
  }),
  startCodexManualAuthorization: () => request<CodexManualAuthorizationStart>('/api/admin/provider-credentials/codex/manual/start', {
    method: 'POST',
  }),
  completeCodexManualAuthorization: (sessionId: string, authorizationResponse: string) => request<CodexManualAuthorizationCompleteResult>('/api/admin/provider-credentials/codex/manual/complete', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, authorization_response: authorizationResponse }),
  }),
  startCodexDeviceFlow: (scopes?: string[]) => request<CodexDeviceStart>('/api/admin/provider-credentials/codex/device/start', {
    method: 'POST',
    body: JSON.stringify({ scopes: scopes?.length ? scopes : null }),
  }),
  codexDeviceFlowStatus: (sessionId: string) => request<CodexSessionStatus>(`/api/admin/provider-credentials/codex/device/status/${sessionId}`),
  pollCodexDeviceFlow: (sessionId: string) => request<CodexSessionStatus>(`/api/admin/provider-credentials/codex/device/poll/${sessionId}`, { method: 'POST' }),
  refreshCodexCredential: (credentialId: string) => request<CodexCredentialActionResult>(`/api/admin/provider-credentials/codex/refresh/${credentialId}`, { method: 'POST' }),
  revokeCodexCredential: (credentialId: string) => request<CodexCredentialActionResult>(`/api/admin/provider-credentials/codex/revoke/${credentialId}`, { method: 'POST' }),
  disconnectCodexCredential: (credentialId: string) => request<CodexCredentialActionResult>(`/api/admin/provider-credentials/codex/disconnect/${credentialId}`, { method: 'POST' }),
  outboundChannelCapabilities: () => request<OutboundChannelCapabilitiesResponse>('/api/outbound/channels/capabilities'),

  webchatConversations: (init?: RequestInit) => request<WebchatConversation[]>('/api/webchat/admin/conversations', init),
  webchatHandoffQueue: (params?: { view?: string; include_declined?: boolean; limit?: number }, init?: RequestInit) => {
    const search = new URLSearchParams()
    search.set('view', params?.view || 'requested')
    search.set('limit', String(params?.limit ?? 50))
    if (typeof params?.include_declined === 'boolean') search.set('include_declined', String(params.include_declined))
    return request<WebchatHandoffQueue>(`/api/webchat/admin/handoff/queue?${search.toString()}`, init)
  },
  webchatAcceptHandoff: (requestId: number, note?: string) => request<WebchatHandoffRequest>(`/api/webchat/admin/handoff/${requestId}/accept`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  }),
  webchatDeclineHandoff: (requestId: number, payload?: { reason_code?: string; note?: string }) => request<WebchatHandoffRequest>(`/api/webchat/admin/handoff/${requestId}/decline`, {
    method: 'POST',
    body: JSON.stringify({ reason_code: payload?.reason_code || null, note: payload?.note || null }),
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
  webchatThread: (ticketId: number, init?: RequestInit) => request<WebchatThread>(`/api/webchat/admin/tickets/${ticketId}/thread`, init),
  webchatEvents: (ticketId: number, afterId: number, init?: RequestInit) => request<WebchatEventsPage>(`/api/webchat/admin/tickets/${ticketId}/events?${buildWebchatEventsSearch(afterId).toString()}`, init),
  webchatReadState: (ticketId: number, payload: { marked_unread: boolean }) => request<WebchatReadStateResult>(`/api/webchat/admin/tickets/${ticketId}/read-state`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  webchatReply: (ticketId: number, payload: { body: string; has_fact_evidence?: boolean; confirm_review?: boolean }) => request<WebchatReplyResult>(`/api/webchat/admin/tickets/${ticketId}/reply`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  webchatVoiceRuntimeConfig: (init?: RequestInit) => request<WebchatVoiceRuntimeConfig>('/api/webchat/voice/runtime-config', init),
  webchatVoiceIncomingSessions: (params?: { status?: string; limit?: number }, init?: RequestInit) => {
    const search = new URLSearchParams()
    search.set('status', params?.status || 'ringing')
    search.set('limit', String(params?.limit ?? 50))
    return request<{ items: WebchatVoiceIncomingSession[] }>(`/api/webchat/admin/voice/sessions?${search.toString()}`, init)
  },
  webchatVoiceSessions: (ticketId: number, init?: RequestInit) => request<{ items: WebchatVoiceSession[] }>(`/api/webchat/admin/tickets/${ticketId}/voice/sessions`, init),
  webchatVoiceAcceptSession: (ticketId: number, voiceSessionId: string) => request<WebchatVoiceSession>(`/api/webchat/admin/tickets/${ticketId}/voice/${voiceSessionId}/accept`, { method: 'POST' }),
  webchatVoiceRejectSession: (ticketId: number, voiceSessionId: string, reason?: string) => request<{ ok: boolean; status: string; voice_session_id: string; accepted_by_user_id?: number | null }>(`/api/webchat/admin/tickets/${ticketId}/voice/${voiceSessionId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason: reason || null }),
  }),
  webchatVoiceEndSession: (ticketId: number, voiceSessionId: string) => request<{ ok: boolean; status: string; voice_session_id: string; accepted_by_user_id?: number | null }>(`/api/webchat/admin/tickets/${ticketId}/voice/${voiceSessionId}/end`, { method: 'POST' }),

  unresolvedEvents: () => request<OpenClawUnresolvedEvent[]>('/api/admin/openclaw/unresolved-events'),
  replayUnresolvedEvent: (eventId: number) => request<{ ok: boolean; linked_ticket_id?: number | null }>(`/api/admin/openclaw/unresolved-events/${eventId}/replay`, {
    method: 'POST',
  }),
  dropUnresolvedEvent: (eventId: number) => request<{ ok: boolean }>(`/api/admin/openclaw/unresolved-events/${eventId}/drop`, {
    method: 'POST',
  }),
}
