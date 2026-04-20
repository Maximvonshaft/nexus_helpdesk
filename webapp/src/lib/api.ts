import type {
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
} from '@/lib/types'

const STORAGE_KEY = 'helpdesk-webapp-token'

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
  const res = await fetch(path, { ...init, headers })
  if (res.status === 401) {
    clearToken()
    throw new Error('登录状态已失效，请重新登录')
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const data = await res.json()
      msg = data?.detail || JSON.stringify(data)
    } catch {}
    throw new Error(msg)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  users: () => request<AuthUser[]>('/api/lookups/users'),
  teams: () => request<any[]>('/api/lookups/teams'),
  capabilityCatalog: () => request<string[]>('/api/admin/capabilities/catalog'),
  createUser: (payload: Record<string, unknown>) => request<AuthUser>('/api/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  login: (username: string, password: string) => request<{access_token: string; user: AuthUser}>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  me: () => request<AuthUser>('/api/auth/me'),

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

  bulletins: () => request<Bulletin[]>('/api/lookups/bulletins'),
  createBulletin: (payload: Partial<Bulletin>) => request<Bulletin>('/api/admin/bulletins', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateBulletin: (bulletinId: number, payload: Partial<Bulletin>) => request<Bulletin>(`/api/admin/bulletins/${bulletinId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
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
}
