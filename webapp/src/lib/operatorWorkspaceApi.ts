import { apiRequest, ApiError } from '@/lib/apiClient'
import type { WebchatReadStateResult, WebchatReplyResult, WebchatThread } from '@/lib/types'
import type {
  AuthorizedWorkspaceScopesResponse,
  UnifiedOperatorQueueResponse,
  WorkspaceFilters,
  WorkspaceScope,
  WorkspaceSourceRecord,
} from '@/lib/operatorWorkspaceTypes'

const WORKSPACE_SCOPE_STORAGE_KEY = 'nexus-operator-workspace-scope'
const UNIFIED_OPERATOR_QUEUE_PATH = '/api/admin/operator-queue/unified'
const CURRENT_OPERATOR_SCOPES_PATH = '/api/admin/operator-queue/my-scopes'

function requireApiPath(path: string) {
  const normalized = String(path || '').trim()
  if (!normalized.startsWith('/api/')) {
    throw new ApiError('服务器返回了不受支持的案例链接', 400)
  }
  return normalized
}

function applyFilter(search: URLSearchParams, key: string, value: string, emptyValues: string[]) {
  if (!emptyValues.includes(value)) search.set(key, value)
}

export function loadWorkspaceScope(): WorkspaceScope {
  const fallback: WorkspaceScope = {
    tenantKey: String(import.meta.env.VITE_NEXUS_TENANT_KEY || '').trim(),
    countryCode: String(import.meta.env.VITE_NEXUS_COUNTRY_CODE || 'CH').trim().toUpperCase(),
    channelKey: String(import.meta.env.VITE_NEXUS_CHANNEL_KEY || 'webchat').trim().toLowerCase(),
  }
  if (typeof window === 'undefined') return fallback
  try {
    const stored = JSON.parse(sessionStorage.getItem(WORKSPACE_SCOPE_STORAGE_KEY) || '{}') as Partial<WorkspaceScope>
    return {
      tenantKey: String(stored.tenantKey || fallback.tenantKey).trim(),
      countryCode: String(stored.countryCode || fallback.countryCode).trim().toUpperCase(),
      channelKey: String(stored.channelKey || fallback.channelKey).trim().toLowerCase(),
    }
  } catch {
    return fallback
  }
}

export function saveWorkspaceScope(scope: WorkspaceScope) {
  if (typeof window === 'undefined') return
  sessionStorage.setItem(WORKSPACE_SCOPE_STORAGE_KEY, JSON.stringify(scope))
}

export const operatorWorkspaceApi = {
  currentScopes: (init?: RequestInit) => apiRequest<AuthorizedWorkspaceScopesResponse>(CURRENT_OPERATOR_SCOPES_PATH, {
    ...init,
    requestIdPrefix: 'workspace-scope',
  }),

  unifiedQueue: (
    scope: WorkspaceScope,
    filters: WorkspaceFilters,
    cursor?: string | null,
    init?: RequestInit,
  ) => {
    const search = new URLSearchParams({
      country_code: scope.countryCode.trim().toUpperCase(),
      channel_key: scope.channelKey.trim().toLowerCase(),
      sort: filters.sort,
      limit: '50',
    })
    applyFilter(search, 'state', filters.state, ['all'])
    applyFilter(search, 'source_type', filters.sourceType, ['all'])
    applyFilter(search, 'owner', filters.owner, ['any'])
    applyFilter(search, 'priority', filters.priority, ['all'])
    applyFilter(search, 'sla', filters.sla, ['any'])
    applyFilter(search, 'retry', filters.retry, ['any'])
    if (cursor) search.set('cursor', cursor)

    const headers = new Headers(init?.headers ?? {})
    headers.set('X-Nexus-Tenant', scope.tenantKey.trim())
    return apiRequest<UnifiedOperatorQueueResponse>(`${UNIFIED_OPERATOR_QUEUE_PATH}?${search.toString()}`, {
      ...init,
      headers,
      requestIdPrefix: 'workspace',
    })
  },

  conversationThread: (path: string, init?: RequestInit) => apiRequest<WebchatThread>(requireApiPath(path), {
    ...init,
    requireApiPath: true,
    requestIdPrefix: 'workspace',
  }),

  sourceRecord: (path: string, init?: RequestInit) => apiRequest<WorkspaceSourceRecord>(requireApiPath(path), {
    ...init,
    requireApiPath: true,
    requestIdPrefix: 'workspace',
  }),

  reply: (ticketId: number, body: string, confirmReview: boolean) => apiRequest<WebchatReplyResult>(`/api/webchat/admin/tickets/${ticketId}/reply`, {
    method: 'POST',
    body: JSON.stringify({
      body,
      confirm_review: confirmReview,
      has_fact_evidence: false,
    }),
    requestIdPrefix: 'workspace',
  }),

  markReadState: (ticketId: number, markedUnread: boolean) => apiRequest<WebchatReadStateResult>(`/api/webchat/admin/tickets/${ticketId}/read-state`, {
    method: 'POST',
    body: JSON.stringify({ marked_unread: markedUnread }),
    requestIdPrefix: 'workspace',
  }),

  declineHandoff: (requestId: number, reasonCode: string, note?: string) => apiRequest(`/api/webchat/admin/handoff/${requestId}/decline`, {
    method: 'POST',
    body: JSON.stringify({ reason_code: reasonCode || null, note: note || null }),
    requestIdPrefix: 'workspace',
  }),
}
