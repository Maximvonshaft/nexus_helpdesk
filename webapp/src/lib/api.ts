import type {
  AdminUser,
  AuthUser,
  BackgroundJob,
  Bulletin,
  CaseDetail,
  CaseListItem,
  ChannelAccount,
  LiteMeta,
  Market,
  ProductionReadiness,
  QueueSummary,
  RuntimeHealth,
  OpenClawConnectivityProbe,
  SignoffChecklist,
  AIConfigResource,
  AIConfigVersion,
  OpenClawUnresolvedEvent,
  Team,
  WebchatConversation,
  WebchatThread,
  WebchatReplyResult,
} from '@/lib/types'

const STORAGE_KEY = 'helpdesk-webapp-token'
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

function buildApiUrl(path: string) {
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}

export function getToken() {
  return sessionStorage.getItem(STORAGE_KEY)
}

export function setToken(token: string | null) {
  if (!token) sessionStorage.removeItem(STORAGE_KEY)
  else sessionStorage.setItem(STORAGE_KEY, token)
}

export function clearToken() {
  sessionStorage.removeItem(STORAGE_KEY)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const headers = new Headers(init?.headers ?? {})
  if (!(init?.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(buildApiUrl(path), { ...init, headers })
  if (res.status === 401) {
    clearToken()
    throw new Error('登录状态已失效，请重新登录')
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const data = await res.json()
      const detail = data?.detail
      msg = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : JSON.stringify(data)
    } catch {
      // ignore non-JSON error bodies and fall back to status text
    }
    throw new Error(msg)
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
