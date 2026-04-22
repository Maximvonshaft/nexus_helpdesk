import type {
  AdminUser,
  AuthUser,
  BackgroundJob,
  Bulletin,
  CaseDetail,
  CaseListItem,
  ChannelAccount,
  ChannelOnboardingTask,
  ChannelRouteExplanation,
  KnowledgeItem,
  KnowledgePreview,
  KnowledgeUploadResult,
  KnowledgeVersion,
  LiteMeta,
  Market,
  PersonaPreview,
  PersonaProfile,
  PersonaVersion,
  ProductionReadiness,
  QueueSummary,
  RuntimeHealth,
  OpenClawConnectivityProbe,
  SignoffChecklist,
  AIConfigResource,
  AIConfigVersion,
  OpenClawUnresolvedEvent,
  Team,
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
    } catch {
      // ignore
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

  personaProfiles: () => request<PersonaProfile[]>('/api/admin/persona-profiles'),
  createPersonaProfile: (payload: Partial<PersonaProfile>) => request<PersonaProfile>('/api/admin/persona-profiles', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updatePersonaProfile: (profileId: number, payload: Partial<PersonaProfile>) => request<PersonaProfile>(`/api/admin/persona-profiles/${profileId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  publishPersonaProfile: (profileId: number, notes?: string) => request<PersonaVersion>(`/api/admin/persona-profiles/${profileId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  personaVersions: (profileId: number) => request<PersonaVersion[]>(`/api/admin/persona-profiles/${profileId}/versions`),
  rollbackPersonaProfile: (profileId: number, version: number, notes?: string) => request<PersonaVersion>(`/api/admin/persona-profiles/${profileId}/rollback/${version}`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  previewPersonaResolution: (payload: Record<string, unknown>) => request<PersonaPreview>('/api/admin/persona-profiles/resolve-preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  knowledgeItems: () => request<KnowledgeItem[]>('/api/admin/knowledge-items'),
  createKnowledgeItem: (payload: Partial<KnowledgeItem>) => request<KnowledgeItem>('/api/admin/knowledge-items', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateKnowledgeItem: (itemId: number, payload: Partial<KnowledgeItem>) => request<KnowledgeItem>(`/api/admin/knowledge-items/${itemId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  publishKnowledgeItem: (itemId: number, notes?: string) => request<KnowledgeVersion>(`/api/admin/knowledge-items/${itemId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  archiveKnowledgeItem: (itemId: number) => request<KnowledgeItem>(`/api/admin/knowledge-items/${itemId}/archive`, {
    method: 'POST',
  }),
  knowledgeVersions: (itemId: number) => request<KnowledgeVersion[]>(`/api/admin/knowledge-items/${itemId}/versions`),
  rollbackKnowledgeItem: (itemId: number, version: number, notes?: string) => request<KnowledgeVersion>(`/api/admin/knowledge-items/${itemId}/rollback/${version}`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  previewKnowledgeResolution: (payload: Record<string, unknown>) => request<KnowledgePreview>('/api/admin/knowledge-items/resolve-preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  uploadKnowledgeFile: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<KnowledgeUploadResult>('/api/admin/knowledge-items/upload', {
      method: 'POST',
      body: form,
    })
  },

  channelControlAccounts: () => request<ChannelAccount[]>('/api/admin/channel-control/accounts'),
  createChannelControlAccount: (payload: Partial<ChannelAccount>) => request<ChannelAccount>('/api/admin/channel-control/accounts', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateChannelControlAccount: (accountId: number, payload: Partial<ChannelAccount>) => request<ChannelAccount>(`/api/admin/channel-control/accounts/${accountId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  explainChannelRoute: (payload: Record<string, unknown>) => request<ChannelRouteExplanation>('/api/admin/channel-control/route-explain', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  onboardingTasks: (provider?: string) => request<ChannelOnboardingTask[]>(`/api/admin/channel-control/onboarding-tasks${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  createOnboardingTask: (payload: Record<string, unknown>) => request<ChannelOnboardingTask>('/api/admin/channel-control/onboarding-tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateOnboardingTask: (taskId: number, payload: Record<string, unknown>) => request<ChannelOnboardingTask>(`/api/admin/channel-control/onboarding-tasks/${taskId}`, {
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

  unresolvedEvents: () => request<OpenClawUnresolvedEvent[]>('/api/admin/openclaw/unresolved-events'),
  replayUnresolvedEvent: (eventId: number) => request<{ ok: boolean; linked_ticket_id?: number | null }>(`/api/admin/openclaw/unresolved-events/${eventId}/replay`, {
    method: 'POST',
  }),
  dropUnresolvedEvent: (eventId: number) => request<{ ok: boolean }>(`/api/admin/openclaw/unresolved-events/${eventId}/drop`, {
    method: 'POST',
  }),
}
