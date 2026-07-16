import { apiRequest, ApiError } from '@/lib/apiClient'
import type { WebchatReadStateResult, WebchatReplyResult, WebchatThread } from '@/lib/types'
import type {
  AuthorizedWorkspaceScopesResponse,
  UnifiedOperatorQueueResponse,
  WorkspaceFilters,
  WorkspaceScope,
  WorkspaceSourceRecord,
} from '@/lib/operatorWorkspaceTypes'

const UNIFIED_OPERATOR_QUEUE_PATH = '/api/admin/operator-queue/unified'
const CURRENT_OPERATOR_SCOPES_PATH = '/api/admin/operator-queue/my-scopes'

export type OperatorWorkspaceThread = WebchatThread & {
  message_page?: {
    before_id?: number | null
    has_more: boolean
    limit: number
  }
}

export type OperatorWorkspaceEventEnvelope = {
  event_id: number
  type: string
  data?: Record<string, unknown> | null
}

export type OperatorWorkspaceEventPage = {
  events: OperatorWorkspaceEventEnvelope[]
  last_event_id: number
  has_more: boolean
  wait_ms: number
}

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

function withQuery(path: string, params: Record<string, string | number | null | undefined>) {
  const normalized = requireApiPath(path)
  const [pathname, existingQuery = ''] = normalized.split('?', 2)
  const search = new URLSearchParams(existingQuery)
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value))
  })
  const suffix = search.toString()
  return suffix ? `${pathname}?${suffix}` : pathname
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

  conversationThread: (
    path: string,
    params?: { beforeMessageId?: number | null; messageLimit?: number },
    init?: RequestInit,
  ) => apiRequest<OperatorWorkspaceThread>(withQuery(path, {
    before_message_id: params?.beforeMessageId,
    message_limit: params?.messageLimit ?? 100,
  }), {
    ...init,
    requireApiPath: true,
    requestIdPrefix: 'workspace',
  }),

  conversationEvents: (
    ticketId: number,
    afterId: number,
    init?: RequestInit,
  ) => apiRequest<OperatorWorkspaceEventPage>(`/api/webchat/admin/tickets/${ticketId}/events?after_id=${Math.max(0, afterId)}&limit=100&wait_ms=10000`, {
    ...init,
    requireApiPath: true,
    requestIdPrefix: 'workspace-events',
  }),

  sourceRecord: (path: string, init?: RequestInit) => apiRequest<WorkspaceSourceRecord>(requireApiPath(path), {
    ...init,
    requireApiPath: true,
    requestIdPrefix: 'workspace',
  }),

  reply: (ticketId: number, body: string) => apiRequest<WebchatReplyResult>(`/api/webchat/admin/tickets/${ticketId}/reply`, {
    method: 'POST',
    body: JSON.stringify({ body }),
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
