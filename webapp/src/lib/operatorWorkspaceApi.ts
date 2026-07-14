import {
  ApiError,
  AuthExpiredError,
  clearSupportToken,
  getSupportToken,
  normalizeSupportApiBaseUrl,
} from '@/lib/supportApi'
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
const API_BASE_URL = normalizeSupportApiBaseUrl(import.meta.env.VITE_API_BASE_URL)
const DEFAULT_API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000)

function apiUrl(path: string) {
  const safePath = requireApiPath(path)
  return `${API_BASE_URL}${safePath}`
}

function requireApiPath(path: string) {
  const normalized = String(path || '').trim()
  if (!normalized.startsWith('/api/')) {
    throw new ApiError('服务器返回了不受支持的案例链接', 400)
  }
  return normalized
}

function createRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `workspace-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

async function readFailure(response: Response, fallback: string) {
  try {
    const data = await response.json() as { detail?: unknown; message?: unknown }
    const detail = data?.detail
    if (typeof detail === 'string' && detail.trim()) return { message: detail, detail }
    if (detail && typeof detail === 'object') {
      const object = detail as { message?: unknown; code?: unknown; error?: unknown }
      const message = object.message ?? object.error ?? object.code
      if (typeof message === 'string' && message.trim()) return { message, detail }
    }
    if (typeof data?.message === 'string' && data.message.trim()) return { message: data.message, detail }
    return { message: fallback, detail }
  } catch {
    return { message: fallback, detail: undefined }
  }
}

async function operatorRequest<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_API_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), Math.max(1000, timeoutMs))
  const headers = new Headers(init.headers ?? {})
  const token = getSupportToken()

  headers.set('Content-Type', 'application/json')
  headers.set('X-Request-Id', headers.get('X-Request-Id') || createRequestId())
  if (token) headers.set('Authorization', `Bearer ${token}`)

  try {
    const response = await fetch(apiUrl(path), {
      ...init,
      headers,
      signal: init.signal ?? controller.signal,
    })
    if (response.status === 401) {
      clearSupportToken()
      throw new AuthExpiredError()
    }
    if (!response.ok) {
      const failure = await readFailure(response, `请求失败：${response.status}`)
      throw new ApiError(failure.message, response.status, failure.detail)
    }
    if (response.status === 204) return undefined as T
    return await response.json() as T
  } catch (error) {
    if (error instanceof ApiError || error instanceof AuthExpiredError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('请求超时。请确认工作范围或稍后重试。', 0)
    }
    throw new ApiError('网络请求失败。已保留当前页面，请稍后重试。', 0)
  } finally {
    window.clearTimeout(timeout)
  }
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
  currentScopes: (init?: RequestInit) =>
    operatorRequest<AuthorizedWorkspaceScopesResponse>(CURRENT_OPERATOR_SCOPES_PATH, init),

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

    return operatorRequest<UnifiedOperatorQueueResponse>(
      `${UNIFIED_OPERATOR_QUEUE_PATH}?${search.toString()}`,
      {
        ...init,
        headers: {
          ...Object.fromEntries(new Headers(init?.headers ?? {}).entries()),
          'X-Nexus-Tenant': scope.tenantKey.trim(),
        },
      },
    )
  },

  conversationThread: (path: string, init?: RequestInit) =>
    operatorRequest<WebchatThread>(requireApiPath(path), init),

  sourceRecord: (path: string, init?: RequestInit) =>
    operatorRequest<WorkspaceSourceRecord>(requireApiPath(path), init),

  reply: (ticketId: number, body: string, confirmReview: boolean) =>
    operatorRequest<WebchatReplyResult>(`/api/webchat/admin/tickets/${ticketId}/reply`, {
      method: 'POST',
      body: JSON.stringify({
        body,
        confirm_review: confirmReview,
        has_fact_evidence: false,
      }),
    }),

  markReadState: (ticketId: number, markedUnread: boolean) =>
    operatorRequest<WebchatReadStateResult>(`/api/webchat/admin/tickets/${ticketId}/read-state`, {
      method: 'POST',
      body: JSON.stringify({ marked_unread: markedUnread }),
    }),

  declineHandoff: (requestId: number, reasonCode: string, note?: string) =>
    operatorRequest(`/api/webchat/admin/handoff/${requestId}/decline`, {
      method: 'POST',
      body: JSON.stringify({ reason_code: reasonCode || null, note: note || null }),
    }),
}
