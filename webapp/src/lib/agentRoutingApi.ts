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
  voice_enabled: boolean
  voice_assignable: boolean
  max_concurrent_voice_calls: number
  active_voice_calls: number
  reserved_voice_offers: number
  available_voice_capacity: number
  voice_wrap_up_seconds: number
  last_heartbeat_at?: string | null
  heartbeat_ttl_seconds: number
}

export interface AgentStateUpdate {
  status: AgentPresenceStatus
  voiceEnabled?: boolean
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

  updateState: ({ status, voiceEnabled }: AgentStateUpdate) => apiRequest<AgentState>('/api/operator/agent-state', {
    method: 'PUT',
    body: JSON.stringify({
      status,
      ...(voiceEnabled === undefined ? {} : { voice_enabled: voiceEnabled }),
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
