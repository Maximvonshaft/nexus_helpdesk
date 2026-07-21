import { apiRequest } from '@/lib/apiClient'
import type {
  AgentConfigResource,
  AgentControlSnapshot,
  AgentDefinition,
  AgentDeployment,
  AgentPersona,
  AgentPlaygroundResult,
  AgentRelease,
} from '@/lib/types'

export interface AgentConfigDraft {
  resource_key: string
  config_type: AgentConfigResource['config_type']
  name: string
  description?: string | null
  scope_type: AgentConfigResource['scope_type']
  scope_value?: string | null
  market_id?: number | null
  is_active: boolean
  draft_summary?: string | null
  draft_content_json: Record<string, unknown>
}

export interface PersonaDraft {
  profile_key: string
  name: string
  description?: string | null
  market_id?: number | null
  channel?: string | null
  language?: string | null
  is_active: boolean
  draft_summary?: string | null
  draft_content_json: Record<string, unknown>
}

export interface AgentDefinitionDraft {
  tenant_key?: string | null
  definition_key: string
  name: string
  purpose?: string | null
  owner_team_id?: number | null
  draft_manifest: Record<string, unknown>
}

export interface AgentDeploymentDraft {
  tenant_key?: string | null
  environment: 'test' | 'staging' | 'production'
  release_id: number
  canary_release_id?: number | null
  canary_percent?: number
  market_id?: number | null
  channel?: string | null
  language?: string | null
  case_type?: string | null
}

export interface AgentScopeDraft {
  tenant_key?: string
  environment?: 'test' | 'staging' | 'production'
  market_id?: number | null
  channel?: string
  language?: string | null
  case_type?: string | null
  cohort_key?: string
}

export interface ToolPolicyDraft {
  tool_name: string
  country_code: string
  channel: string
  enabled: boolean
  ai_auto_executable: boolean
  risk_level: string
  requires_tracking_number: boolean
  requires_contact: boolean
  requires_customer_confirmation: boolean
  requires_human_confirmation: boolean
  allowed_channels_json?: string[] | null
  allowed_countries_json?: string[] | null
  audit_level: string
}

function queryString(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  })
  const rendered = search.toString()
  return rendered ? `?${rendered}` : ''
}

export const agentControlApi = {
  snapshot: (scope?: {
    tenantKey?: string
    environment?: 'test' | 'staging' | 'production'
    marketId?: number | null
    channel?: string
    language?: string | null
    caseType?: string | null
  }) => apiRequest<AgentControlSnapshot>(`/api/agent-control/snapshot${queryString({
    tenant_key: scope?.tenantKey || 'default',
    environment: scope?.environment || 'production',
    market_id: scope?.marketId,
    channel: scope?.channel || 'webchat',
    language: scope?.language,
    case_type: scope?.caseType,
  })}`),

  createDefinition: (payload: AgentDefinitionDraft) =>
    apiRequest<AgentDefinition>('/api/agent-control/definitions', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateDefinition: (
    id: number,
    tenantKey: string,
    payload: Partial<Omit<AgentDefinitionDraft, 'tenant_key' | 'definition_key'>> & { is_active?: boolean },
  ) => apiRequest<AgentDefinition>(
    `/api/agent-control/definitions/${id}${queryString({ tenant_key: tenantKey })}`,
    { method: 'PUT', body: JSON.stringify(payload) },
  ),
  releaseDefinition: (id: number, tenantKey: string) =>
    apiRequest<AgentRelease>(
      `/api/agent-control/definitions/${id}/releases${queryString({ tenant_key: tenantKey })}`,
      { method: 'POST' },
    ),
  deployRelease: (payload: AgentDeploymentDraft) =>
    apiRequest<AgentDeployment>('/api/agent-control/deployments', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  resolve: (payload: AgentScopeDraft) =>
    apiRequest<{ digest: string; snapshot: Record<string, unknown> }>(
      '/api/agent-control/resolve',
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  runs: (tenantKey = 'default', limit = 50) =>
    apiRequest<Array<Record<string, unknown>>>(
      `/api/agent-control/runs${queryString({ tenant_key: tenantKey, limit })}`,
    ),

  playground: (payload: AgentScopeDraft & {
    body: string
    execute_model?: boolean
  }) => apiRequest<AgentPlaygroundResult>('/api/agent-control/playground', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),

  createConfig: (payload: AgentConfigDraft) =>
    apiRequest<AgentConfigResource>('/api/admin/ai-configs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateConfig: (id: number, payload: Partial<AgentConfigDraft>) =>
    apiRequest<AgentConfigResource>(`/api/admin/ai-configs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  publishConfig: (id: number, notes?: string) =>
    apiRequest<Record<string, unknown>>(`/api/admin/ai-configs/${id}/publish`, {
      method: 'POST',
      body: JSON.stringify({ notes: notes || null }),
    }),
  configVersions: (id: number) =>
    apiRequest<Array<Record<string, unknown>>>(`/api/admin/ai-configs/${id}/versions`),
  rollbackConfig: (id: number, version: number, notes?: string) =>
    apiRequest<Record<string, unknown>>(`/api/admin/ai-configs/${id}/rollback/${version}`, {
      method: 'POST',
      body: JSON.stringify({ notes: notes || null }),
    }),

  createPersona: (payload: PersonaDraft) =>
    apiRequest<AgentPersona>('/api/persona-profiles', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updatePersona: (id: number, payload: Partial<PersonaDraft>) =>
    apiRequest<AgentPersona>(`/api/persona-profiles/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  publishPersona: (id: number, notes?: string) =>
    apiRequest<Record<string, unknown>>(`/api/persona-profiles/${id}/publish`, {
      method: 'POST',
      body: JSON.stringify({ notes: notes || null }),
    }),
  rollbackPersona: (id: number, version: number, notes?: string) =>
    apiRequest<Record<string, unknown>>(`/api/persona-profiles/${id}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ version, notes: notes || null }),
    }),
  personaRuntimeEvidence: (payload: Record<string, unknown>) =>
    apiRequest<Record<string, unknown>>('/api/persona-profiles/runtime-evidence', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  createToolPolicy: (tenantKey: string, payload: ToolPolicyDraft) =>
    apiRequest<Record<string, unknown>>('/api/admin/osr/tool-execution-policies', {
      method: 'POST',
      headers: { 'X-Nexus-Tenant': tenantKey },
      body: JSON.stringify(payload),
    }),
  updateToolPolicy: (tenantKey: string, id: number, payload: Partial<ToolPolicyDraft>) =>
    apiRequest<Record<string, unknown>>(`/api/admin/osr/tool-execution-policies/${id}`, {
      method: 'PATCH',
      headers: { 'X-Nexus-Tenant': tenantKey },
      body: JSON.stringify(payload),
    }),

  testIntegration: (payload: AgentScopeDraft & {
    integration_key: string
    operation: string
    arguments?: Record<string, unknown>
  }) => apiRequest<Record<string, unknown>>('/api/agent-control/integrations/test', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
}
