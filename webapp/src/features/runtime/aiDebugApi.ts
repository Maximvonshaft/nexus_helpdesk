import { apiRequest } from '@/lib/apiClient'

export type AiDebugRun = {
  id: number
  conversation_id: number
  ticket_id: number
  ai_turn_id: number
  channel?: string | null
  status: string
  intent?: string | null
  tracking_intent_detected: boolean
  tracking_fact_evidence_present: boolean
  live_tracking_answer_allowed: boolean
  kb_hits_count: number
  tool_call_count: number
  customer_visible_message_created: boolean
  created_at?: string | null
}

export type AiDebugBundle = Record<string, unknown>
export type AiDebugFinding = Record<string, unknown> & { id?: number }

export const aiDebugApi = {
  listDebugRuns: (params: {
    since_hours: number
    channel?: string
    tracking_fact_evidence_present?: boolean
    live_tracking_answer_allowed?: boolean
    customer_visible_message_created?: boolean
    limit: number
  }) => {
    const search = new URLSearchParams({ since_hours: String(params.since_hours), limit: String(params.limit) })
    if (params.channel) search.set('channel', params.channel)
    if (typeof params.tracking_fact_evidence_present === 'boolean') search.set('tracking_fact_evidence_present', String(params.tracking_fact_evidence_present))
    if (typeof params.live_tracking_answer_allowed === 'boolean') search.set('live_tracking_answer_allowed', String(params.live_tracking_answer_allowed))
    if (typeof params.customer_visible_message_created === 'boolean') search.set('customer_visible_message_created', String(params.customer_visible_message_created))
    return apiRequest<{ items: AiDebugRun[]; total: number }>(`/api/webchat/admin/debug-runs?${search.toString()}`, {
      requestIdPrefix: 'runtime-audit',
    })
  },
  getDebugBundle: (aiTurnId: number) => apiRequest<AiDebugBundle>(`/api/webchat/admin/ai-turns/${aiTurnId}/debug-bundle`, {
    requestIdPrefix: 'runtime-audit',
  }),
  createFinding: (aiTurnId: number, payload: Record<string, unknown>) => apiRequest<AiDebugFinding>(`/api/webchat/admin/ai-turns/${aiTurnId}/test-findings`, {
    method: 'POST',
    body: JSON.stringify(payload),
    requestIdPrefix: 'runtime-audit',
  }),
  createEvalCase: (findingId: number) => apiRequest<Record<string, unknown>>(`/api/webchat/admin/test-findings/${findingId}/eval-case`, {
    method: 'POST',
    requestIdPrefix: 'runtime-audit',
  }),
}
