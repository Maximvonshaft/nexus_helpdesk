import { apiRequest } from '@/lib/apiClient'

export interface RoleTemplate {
  id: number
  tenant_id?: number | null
  role_key: string
  display_name: string
  description?: string | null
  base_role: string
  risk_level: string
  is_system_protected: boolean
  is_active: boolean
  draft_capabilities: string[]
  published_capabilities: string[]
  published_version: number
  published_at?: string | null
  assignment_count: number
  can_manage: boolean
  updated_at: string
}

export interface RoleTemplateAssignment {
  user_id: number
  display_name: string
  username: string
  assignment?: {
    template_id: number
    template_version: number
    template_name: string
    assigned_at: string
    drifted: boolean
  } | null
}

export interface CountryCatalogItem {
  iso_alpha2: string
  iso_alpha3: string
  iso_numeric?: string | null
  canonical_name: string
  calling_code?: string | null
  default_currency?: string | null
  is_available: boolean
}

export interface GovernanceTeam {
  id: number
  name: string
}

export interface MarketImpact {
  teams: number
  tickets: number
  knowledge: number
  personas: number
  agent_configs: number
  deployments: number
  channels: number
  email_accounts: number
  bulletins: number
}

export interface GovernedMarket {
  id: number
  tenant_id?: number | null
  code: string
  name: string
  country_code: string
  language_code?: string | null
  timezone?: string | null
  is_active: boolean
  status: 'draft' | 'active' | 'paused' | 'retiring' | 'retired'
  default_currency?: string | null
  owner_team_id?: number | null
  data_region?: string | null
  notes?: string | null
  version: number
  countries: string[]
  languages: string[]
  impact: MarketImpact
  updated_at: string
}

export interface KnowledgeImportDocument {
  id: number
  position: number
  file_name: string
  sha256: string
  status: 'draft_created' | 'duplicate' | 'failed'
  knowledge_item_id?: number | null
  duplicate_of_document_id?: number | null
  error_code?: string | null
  error_message?: string | null
  created_at: string
}

export interface KnowledgeImportBatch {
  id: number
  status: 'processing' | 'ready' | 'partial' | 'failed'
  total_files: number
  succeeded_files: number
  failed_files: number
  duplicate_files: number
  market_id?: number | null
  channel: string
  audience_scope: 'customer' | 'internal'
  language?: string | null
  created_at: string
  completed_at?: string | null
  documents: KnowledgeImportDocument[]
}

export interface DeploymentDelivery {
  deployment: {
    active_release_id: number
    canary_release_id?: number | null
    canary_percent: number
    is_active: boolean
    environment: string
    scope_key: string
  }
  traffic_24h: { stable: number; trial: number; total: number }
  health_24h: {
    stable: DeliveryHealth
    trial: DeliveryHealth
  }
  revisions: Array<{
    id: number
    revision: number
    action: string
    before: Record<string, unknown>
    after: Record<string, unknown>
    reason?: string | null
    created_by?: number | null
    created_at: string
  }>
}

export interface DeliveryHealth {
  total: number
  succeeded: number
  fallback: number
  failed: number
  average_ms: number
}

export interface RoleTemplateDraft {
  role_key: string
  display_name: string
  description?: string | null
  base_role: string
  risk_level: string
  capabilities: string[]
}

export interface MarketDraft {
  code: string
  name: string
  timezone?: string | null
  status: GovernedMarket['status']
  default_currency?: string | null
  owner_team_id?: number | null
  data_region?: string | null
  notes?: string | null
  country_codes: string[]
  language_codes: string[]
}

export const governanceApi = {
  capabilities: () => apiRequest<string[]>('/api/governance/capabilities'),
  roleTemplates: () => apiRequest<RoleTemplate[]>('/api/governance/role-templates'),
  createRoleTemplate: (payload: RoleTemplateDraft) => apiRequest<RoleTemplate>('/api/governance/role-templates', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateRoleTemplate: (id: number, payload: Partial<Omit<RoleTemplateDraft, 'role_key'>> & { is_active?: boolean }) =>
    apiRequest<RoleTemplate>(`/api/governance/role-templates/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  publishRoleTemplate: (id: number, notes?: string | null) =>
    apiRequest<Record<string, unknown>>(`/api/governance/role-templates/${id}/publish`, {
      method: 'POST',
      body: JSON.stringify({ notes: notes || null }),
    }),
  roleAssignments: () => apiRequest<RoleTemplateAssignment[]>('/api/governance/role-template-assignments'),
  applyRoleTemplate: (templateId: number, userId: number) =>
    apiRequest<Record<string, unknown>>(`/api/governance/role-templates/${templateId}/apply/${userId}`, {
      method: 'POST',
    }),

  countries: () => apiRequest<CountryCatalogItem[]>('/api/governance/countries'),
  marketTeams: () => apiRequest<GovernanceTeam[]>('/api/governance/market-teams'),
  markets: () => apiRequest<GovernedMarket[]>('/api/governance/markets'),
  createMarket: (payload: MarketDraft) => apiRequest<GovernedMarket>('/api/governance/markets', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateMarket: (id: number, payload: Partial<MarketDraft> & { expected_version: number }) =>
    apiRequest<GovernedMarket>(`/api/governance/markets/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  marketImpact: (id: number) => apiRequest<MarketImpact>(`/api/governance/markets/${id}/impact`),

  knowledgeImports: (limit = 20) =>
    apiRequest<KnowledgeImportBatch[]>(`/api/governance/knowledge-imports?limit=${limit}`),
  createKnowledgeImport: (payload: {
    files: File[]
    marketId?: number | null
    channel: string
    audienceScope: 'customer' | 'internal'
    language?: string | null
  }) => {
    const form = new FormData()
    payload.files.forEach((file) => form.append('files', file))
    if (payload.marketId) form.append('market_id', String(payload.marketId))
    form.append('channel', payload.channel)
    form.append('audience_scope', payload.audienceScope)
    if (payload.language?.trim()) form.append('language', payload.language.trim())
    return apiRequest<KnowledgeImportBatch>('/api/governance/knowledge-imports', {
      method: 'POST',
      body: form,
      timeoutMs: 120000,
    })
  },

  deploymentDelivery: (deploymentId: number) =>
    apiRequest<DeploymentDelivery>(`/api/governance/deployments/${deploymentId}/delivery`),
  startTrial: (deploymentId: number, payload: { release_id: number; percent: number; reason: string }) =>
    apiRequest<Record<string, unknown>>(`/api/governance/deployments/${deploymentId}/trial/start`, {
      method: 'POST', body: JSON.stringify(payload),
    }),
  adjustTrial: (deploymentId: number, payload: { percent: number; reason: string }) =>
    apiRequest<Record<string, unknown>>(`/api/governance/deployments/${deploymentId}/trial/adjust`, {
      method: 'POST', body: JSON.stringify(payload),
    }),
  pauseTrial: (deploymentId: number, reason: string) =>
    apiRequest<Record<string, unknown>>(`/api/governance/deployments/${deploymentId}/trial/pause`, {
      method: 'POST', body: JSON.stringify({ reason }),
    }),
  promoteTrial: (deploymentId: number, reason: string) =>
    apiRequest<Record<string, unknown>>(`/api/governance/deployments/${deploymentId}/trial/promote`, {
      method: 'POST', body: JSON.stringify({ reason }),
    }),
}
