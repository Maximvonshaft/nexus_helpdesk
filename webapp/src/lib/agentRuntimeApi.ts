import { apiRequest } from '@/lib/apiClient'

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

export const agentRuntimeApi = {
  doctorMcp: (payload: AgentRuntimeScope & { integration_key: string }) =>
    apiRequest<MCPDoctorReport>('/api/agent-control/integrations/mcp/doctor', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
