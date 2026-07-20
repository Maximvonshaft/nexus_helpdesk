import { apiRequest } from '@/lib/apiClient'

export type AgentPresenceStatus = 'offline' | 'online' | 'paused'

export interface AgentState {
  user_id: number
  status: AgentPresenceStatus
  heartbeat_fresh: boolean
  assignable: boolean
  max_concurrent_conversations: number
  active_conversations: number
  available_capacity: number
  last_heartbeat_at?: string | null
  heartbeat_ttl_seconds: number
}

export interface ConversationCloseResult {
  conversation_id: string
  status: string
  outcome: string
  ticket_id?: number | null
}

export const agentRoutingApi = {
  state: () => apiRequest<AgentState>('/api/operator/agent-state', {
    requestIdPrefix: 'agent-state',
  }),

  updateState: (
    status: AgentPresenceStatus,
    maxConcurrentConversations?: number,
  ) => apiRequest<AgentState>('/api/operator/agent-state', {
    method: 'PUT',
    body: JSON.stringify({
      status,
      max_concurrent_conversations: maxConcurrentConversations,
    }),
    requestIdPrefix: 'agent-state',
  }),

  heartbeat: () => apiRequest<AgentState>('/api/operator/agent-state/heartbeat', {
    method: 'POST',
    requestIdPrefix: 'agent-heartbeat',
  }),

  acceptHandoff: (requestId: number) => apiRequest(`/api/operator/handoffs/${requestId}/accept`, {
    method: 'POST',
    requestIdPrefix: 'agent-handoff',
  }),

  conversationThreadPath: (conversationId: string) => `/api/operator/conversations/${encodeURIComponent(conversationId)}/thread`,

  reply: (conversationId: string, body: string) => apiRequest(`/api/operator/conversations/${encodeURIComponent(conversationId)}/reply`, {
    method: 'POST',
    body: JSON.stringify({ body }),
    requestIdPrefix: 'agent-conversation',
  }),

  closeConversation: (conversationId: string, outcome: string, note?: string) => apiRequest<ConversationCloseResult>(`/api/operator/conversations/${encodeURIComponent(conversationId)}/close`, {
    method: 'POST',
    body: JSON.stringify({ outcome, note: note || null }),
    requestIdPrefix: 'agent-conversation',
  }),
}
