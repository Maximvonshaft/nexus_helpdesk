import type { WebchatThread } from '@/lib/types'

export type WorkspaceSourceType = 'handoff' | 'ticket' | 'dispatch'
export type WorkspaceStateFilter = 'all' | 'active' | 'terminal'
export type WorkspaceOwnerFilter = 'any' | 'mine' | 'unassigned' | 'team'
export type WorkspaceSlaFilter = 'any' | 'healthy' | 'at_risk' | 'breached' | 'paused' | 'stale' | 'unavailable'
export type WorkspaceRetryFilter = 'any' | 'pending' | 'processing' | 'retry_scheduled' | 'exhausted' | 'settled'
export type WorkspaceSort = 'oldest' | 'newest'
export type WorkspaceMobileView = 'queue' | 'case' | 'conversation' | 'actions'

export interface WorkspaceScope {
  tenantKey: string
  countryCode: string
  channelKey: string
}

export interface AuthorizedWorkspaceScope {
  tenant_key: string
  tenant_hash: string
  country_code: string
  channel_key: string
}

export interface AuthorizedWorkspaceScopesResponse {
  items: AuthorizedWorkspaceScope[]
}

export function workspaceScopeFromAuthorized(scope: AuthorizedWorkspaceScope): WorkspaceScope {
  return {
    tenantKey: scope.tenant_key,
    countryCode: scope.country_code,
    channelKey: scope.channel_key,
  }
}

export function workspaceScopeKey(scope: WorkspaceScope) {
  return `${scope.tenantKey}\u0000${scope.countryCode}\u0000${scope.channelKey}`
}

export interface WorkspaceFilters {
  state: WorkspaceStateFilter
  sourceType: 'all' | WorkspaceSourceType
  owner: WorkspaceOwnerFilter
  priority: 'all' | 'low' | 'medium' | 'high' | 'urgent'
  sla: WorkspaceSlaFilter
  retry: WorkspaceRetryFilter
  sort: WorkspaceSort
}

export interface UnifiedQueueOwner {
  kind: 'user' | 'team' | 'worker_lease' | 'unassigned'
  user_id: number | null
  team_id: number | null
}

export interface UnifiedQueueSla {
  state: 'healthy' | 'at_risk' | 'breached' | 'paused' | 'stale' | 'not_applicable' | 'unavailable'
  due_at: string | null
  seconds_remaining: number | null
}

export interface UnifiedQueueRetry {
  state: 'not_applicable' | 'pending' | 'processing' | 'retry_scheduled' | 'exhausted' | 'settled'
  attempt_count: number
  max_attempts: number
  next_retry_at: string | null
  error_category: string | null
}

export interface UnifiedQueueSourceLinks {
  ticket: string | null
  conversation: string | null
  handoff: string | null
  dispatch: string | null
}

export interface UnifiedOperatorQueueItem {
  queue_id: string
  case_key: string | null
  source_type: 'handoff' | 'ticket' | 'dispatch'
  source_id: number
  ticket_id: number | null
  conversation_id: number | null
  country_code: string
  channel_key: string
  state: 'active' | 'terminal'
  source_status: string
  reopened: boolean
  priority: 'low' | 'medium' | 'high' | 'urgent'
  owner: UnifiedQueueOwner
  sla: UnifiedQueueSla
  retry: UnifiedQueueRetry
  created_at: string
  updated_at: string
  source_links: UnifiedQueueSourceLinks
}

export interface UnifiedQueueScope {
  tenant_hash: string
  country_code: string
  channel_key: string
}

export interface UnifiedOperatorQueueResponse {
  items: UnifiedOperatorQueueItem[]
  next_cursor: string | null
  scope: UnifiedQueueScope
  filters: {
    state: string | null
    source_type: string | null
    owner: string | null
    priority: string | null
    sla: string | null
    retry: string | null
    sort: WorkspaceSort
  }
}

export interface WorkspaceConversationResult {
  thread: WebchatThread | null
  unavailableReason: string | null
}

export interface WorkspaceSourceRecord {
  id?: number
  ticket_no?: string | null
  title?: string | null
  status?: string | null
  priority?: string | null
  required_action?: string | null
  conversation_state?: string | null
  [key: string]: unknown
}
