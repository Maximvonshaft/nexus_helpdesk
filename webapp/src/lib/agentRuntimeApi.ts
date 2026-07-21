import { apiRequest } from '@/lib/apiClient'

function queryString(params: Record<string, string | number | null | undefined>) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  })
  const rendered = search.toString()
  return rendered ? `?${rendered}` : ''
}

export type MCPDoctorCheck = {
  label: string
  passed: boolean
  detail?: string | null
}

export type MCPDoctorReport = {
  integration_key: string
  healthy: boolean
  protocol_version?: string | null
  server_info: Record<string, unknown>
  capabilities: Record<string, unknown>
  configured_tool_count: number
  discovered_tool_count: number
  schema_digest?: string | null
  checks: MCPDoctorCheck[]
  missing_tools: string[]
  schema_mismatches: string[]
  unmanaged_tools: string[]
  elapsed_ms: number
  tenant_key: string
  agent_release_id: number
  agent_release_version: number
  agent_release_digest: string
  deployment_id: number
}

export type AgentRuntimeScope = {
  tenant_key?: string
  environment: 'test' | 'staging' | 'production'
  market_id?: number | null
  channel?: string | null
  language?: string | null
  case_type?: string | null
}

export type AgentRun = {
  id: number
  request_id: string
  session_id: string
  tenant_key: string
  trace_id: string
  deployment_id?: number | null
  release_id?: number | null
  release_digest?: string | null
  parent_run_id?: number | null
  fork_kind?: 'playground' | 'replay' | null
  status: 'running' | 'succeeded' | 'fallback' | 'failed' | 'cancelled'
  final_action?: string | null
  error_code?: string | null
  elapsed_ms: number
  started_at: string
  completed_at?: string | null
}

export type AgentRunEvent = {
  id: number
  run_id: number
  sequence: number
  event_type: string
  round_index?: number | null
  parent_event_id?: number | null
  status: string
  duration_ms: number
  safe_payload: Record<string, unknown>
  created_at: string
}

export type AgentRunEvents = {
  run: AgentRun
  events: AgentRunEvent[]
  last_sequence: number
}

export const agentRuntimeApi = {
  doctorMcp: (payload: AgentRuntimeScope & { integration_key: string }) =>
    apiRequest<MCPDoctorReport>('/api/agent-control/integrations/mcp/doctor', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  runs: (tenantKey?: string, status?: AgentRun['status'], limit = 50) =>
    apiRequest<AgentRun[]>(`/api/agent-control/runs/lifecycle${queryString({
      tenant_key: tenantKey,
      status,
      limit,
    })}`),
  run: (runId: number, tenantKey?: string) =>
    apiRequest<AgentRun>(`/api/agent-control/runs/${runId}${queryString({
      tenant_key: tenantKey,
    })}`),
  runEvents: (runId: number, tenantKey?: string, afterSequence = 0) =>
    apiRequest<AgentRunEvents>(`/api/agent-control/runs/${runId}/events${queryString({
      tenant_key: tenantKey,
      after_sequence: afterSequence,
      limit: 500,
    })}`),
}
