import type {
  AuthUser,
  ChannelAccount,
  ControlTower,
  KnowledgeItem,
  KnowledgeItemDetail,
  KnowledgeItemList,
  KnowledgeItemVersion,
  KnowledgeRetrievalTestResult,
  KnowledgeStudio,
  ProviderRuntimeStatus,
  SupportConversationMetrics,
  SupportConversationPage,
  SupportConversationReplyPayload,
  SupportConversationReplyResult,
  SupportConversationResolution,
  SupportConversationState,
  WebchatHandoffRequest,
  WhatsAppNativeAccountStatus,
} from '@/lib/types'
import type {
  ChannelOnboardingTask,
  ChannelOnboardingTaskComplete,
  ChannelOnboardingTaskCreate,
  ChannelOnboardingTaskList,
} from '@/lib/channelControlTypes'
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
import type {
  TicketClosureEvidenceRequest,
  TicketClosureEvidenceResult,
  TicketClosureReceipt,
} from '@/lib/ticketClosureTypes'
import { apiRequest, normalizeApiBaseUrl } from '@/lib/apiClient'

export {
  ApiError,
  AuthExpiredError,
  clearSupportToken,
  getSupportToken,
  setSupportToken,
} from '@/lib/apiClient'

export const normalizeSupportApiBaseUrl = normalizeApiBaseUrl

export const supportApi = {
  login: (username: string, password: string) => apiRequest<{ access_token: string; user: AuthUser }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  me: () => apiRequest<AuthUser>('/api/auth/me'),

  controlTower: () => apiRequest<ControlTower>('/api/lite/control-tower'),
  supportConversations: (params?: { view?: string; channel?: string; q?: string; limit?: number }, init?: RequestInit) => {
    const search = new URLSearchParams()
    search.set('view', params?.view || 'open')
    search.set('channel', params?.channel || 'all')
    search.set('limit', String(params?.limit ?? 80))
    if (params?.q?.trim()) search.set('q', params.q.trim())
    return apiRequest<SupportConversationPage>(`/api/support/conversations?${search.toString()}`, init)
  },
  resolveSupportConversation: (sessionKey: string, init?: RequestInit) => {
    const search = new URLSearchParams({ session_key: sessionKey })
    return apiRequest<SupportConversationResolution>(`/api/support/conversations/resolve?${search.toString()}`, init)
  },
  supportConversationMetrics: (sinceHours = 24, init?: RequestInit) => apiRequest<SupportConversationMetrics>(`/api/support/conversations/metrics?since_hours=${sinceHours}`, init),
  supportConversationState: (init?: RequestInit) => apiRequest<SupportConversationState>('/api/support/conversations/state', init),
  supportConversationReply: (payload: SupportConversationReplyPayload) => apiRequest<SupportConversationReplyResult>('/api/support/conversations/reply', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  knowledgeStudio: () => apiRequest<KnowledgeStudio>('/api/lite/knowledge-studio'),
  knowledgeItems: (params?: { q?: string; status?: string; source_type?: string; knowledge_kind?: string; channel?: string; audience_scope?: string }) => {
    const search = new URLSearchParams({ limit: '200' })
    if (params?.q?.trim()) search.set('q', params.q.trim())
    if (params?.status?.trim()) search.set('status', params.status.trim())
    if (params?.source_type?.trim()) search.set('source_type', params.source_type.trim())
    if (params?.knowledge_kind?.trim()) search.set('knowledge_kind', params.knowledge_kind.trim())
    if (params?.channel?.trim()) search.set('channel', params.channel.trim())
    if (params?.audience_scope?.trim()) search.set('audience_scope', params.audience_scope.trim())
    return apiRequest<KnowledgeItemList>(`/api/knowledge-items?${search.toString()}`)
  },
  knowledgeItem: (itemId: number) => apiRequest<KnowledgeItemDetail>(`/api/knowledge-items/${itemId}`),
  createKnowledgeItem: (payload: Partial<KnowledgeItem>) => apiRequest<KnowledgeItem>('/api/knowledge-items', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateKnowledgeItem: (itemId: number, payload: Partial<KnowledgeItem>) => apiRequest<KnowledgeItem>(`/api/knowledge-items/${itemId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  publishKnowledgeItem: (itemId: number, notes?: string) => apiRequest<KnowledgeItemVersion>(`/api/knowledge-items/${itemId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes || null }),
  }),
  testKnowledgeRetrieval: (payload: { q: string; market_id?: number | null; channel?: string | null; audience_scope?: string | null; language?: string | null; limit?: number }) => apiRequest<KnowledgeRetrievalTestResult>('/api/knowledge-items/retrieve-test', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  channelAccounts: () => apiRequest<ChannelAccount[]>('/api/admin/channel-accounts'),
  whatsappNativeStatus: (accountId: string) => apiRequest<WhatsAppNativeAccountStatus>(`/api/admin/whatsapp/accounts/${encodeURIComponent(accountId)}/status`),
  channelOnboardingTasks: (params?: { provider?: string; status?: string; limit?: number; offset?: number }) => {
    const search = new URLSearchParams({
      limit: String(params?.limit ?? 50),
      offset: String(params?.offset ?? 0),
    })
    if (params?.provider?.trim()) search.set('provider', params.provider.trim())
    if (params?.status?.trim()) search.set('status', params.status.trim())
    return apiRequest<ChannelOnboardingTaskList>(`/api/channel-control/onboarding-tasks?${search.toString()}`)
  },
  createChannelOnboardingTask: (payload: ChannelOnboardingTaskCreate) => apiRequest<ChannelOnboardingTask>('/api/channel-control/onboarding-tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  startChannelOnboardingTask: (taskId: number) => apiRequest<ChannelOnboardingTask>(`/api/channel-control/onboarding-tasks/${taskId}/start`, {
    method: 'POST',
  }),
  completeChannelOnboardingTask: (taskId: number, payload: ChannelOnboardingTaskComplete) => apiRequest<ChannelOnboardingTask>(`/api/channel-control/onboarding-tasks/${taskId}/complete`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  failChannelOnboardingTask: (taskId: number, lastError: string) => apiRequest<ChannelOnboardingTask>(`/api/channel-control/onboarding-tasks/${taskId}/fail`, {
    method: 'POST',
    body: JSON.stringify({ last_error: lastError }),
  }),
  cancelChannelOnboardingTask: (taskId: number) => apiRequest<ChannelOnboardingTask>(`/api/channel-control/onboarding-tasks/${taskId}/cancel`, {
    method: 'POST',
  }),
  providerRuntimeStatus: () => apiRequest<ProviderRuntimeStatus>('/api/admin/provider-runtime/status'),

  querySpeedafWaybills: (ticketId: number, payload: SpeedafWaybillLookupPayload) => apiRequest<SpeedafWaybillLookupResponse>(`/api/tickets/${ticketId}/speedaf/waybills/query`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  createSpeedafWorkOrder: (ticketId: number, payload: SpeedafWorkOrderPayload) => apiRequest<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/work-orders`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  submitSpeedafAddressUpdate: (ticketId: number, payload: SpeedafAddressUpdatePayload) => apiRequest<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/address-update`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  previewSpeedafCancel: (ticketId: number, payload: SpeedafCancelPreviewPayload) => apiRequest<SpeedafCancelPreviewResponse>(`/api/tickets/${ticketId}/speedaf/cancel-preview`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  confirmSpeedafCancel: (ticketId: number, payload: SpeedafCancelPayload) => apiRequest<SpeedafActionResponse>(`/api/tickets/${ticketId}/speedaf/cancel`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  ticketClosureReadiness: (ticketId: number, init?: RequestInit) => apiRequest<TicketClosureReceipt>(`/api/tickets/${ticketId}/closure-readiness`, init),
  recordTicketClosureEvidence: (ticketId: number, payload: TicketClosureEvidenceRequest) => apiRequest<TicketClosureEvidenceResult>(`/api/tickets/${ticketId}/closure-evidence`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  closeTicket: (ticketId: number, note?: string) => apiRequest<unknown>(`/api/tickets/${ticketId}/status`, {
    method: 'POST',
    body: JSON.stringify({ new_status: 'closed', note: note || null }),
  }),

  webchatAcceptHandoff: (requestId: number, note?: string) => apiRequest<WebchatHandoffRequest>(`/api/webchat/admin/handoff/${requestId}/accept`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  }),
  webchatForceTakeover: (ticketId: number, payload?: { reason_code?: string; note?: string }) => apiRequest<WebchatHandoffRequest>(`/api/webchat/admin/tickets/${ticketId}/force-takeover`, {
    method: 'POST',
    body: JSON.stringify({ reason_code: payload?.reason_code || null, note: payload?.note || null }),
  }),
  webchatReleaseHandoff: (requestId: number, note?: string) => apiRequest<WebchatHandoffRequest>(`/api/webchat/admin/handoff/${requestId}/release`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  }),
  webchatResumeAi: (requestId: number, note?: string) => apiRequest<WebchatHandoffRequest>(`/api/webchat/admin/handoff/${requestId}/resume-ai`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null }),
  }),
}
