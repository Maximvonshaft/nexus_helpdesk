export type AgentConfigType = 'playbook' | 'integration' | 'model_profile' | 'runtime_policy'

export interface AgentConfigResource {
  id: number
  resource_key: string
  config_type: AgentConfigType
  name: string
  description?: string | null
  scope_type: 'global' | 'market' | 'channel'
  scope_value?: string | null
  market_id?: number | null
  is_active: boolean
  draft_summary?: string | null
  draft_content_json?: Record<string, unknown> | null
  published_summary?: string | null
  published_content_json?: Record<string, unknown> | null
  published_version: number
  published_at?: string | null
  created_at: string
  updated_at: string
}

export interface AgentDefinition {
  id: number
  tenant_key: string
  definition_key: string
  name: string
  purpose?: string | null
  owner_team_id?: number | null
  is_active: boolean
  draft_manifest: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface AgentRelease {
  id: number
  definition_id: number
  version: number
  status: 'approved' | 'canary' | 'active' | 'retired'
  manifest: Record<string, unknown>
  manifest_sha256: string
  validation?: Record<string, unknown> | null
  created_at: string
  approved_at?: string | null
}

export interface AgentDeployment {
  id: number
  tenant_key: string
  environment: 'test' | 'staging' | 'production'
  scope_key: string
  market_id?: number | null
  channel?: string | null
  language?: string | null
  case_type?: string | null
  active_release_id: number
  canary_release_id?: number | null
  canary_percent: number
  is_active: boolean
  activated_at: string
  updated_at: string
}

export interface AgentPersona {
  id: number
  profile_key: string
  name: string
  description?: string | null
  market_id?: number | null
  channel?: string | null
  language?: string | null
  is_active: boolean
  draft_summary?: string | null
  draft_content_json?: Record<string, unknown> | null
  published_summary?: string | null
  published_content_json?: Record<string, unknown> | null
  published_version: number
  published_at?: string | null
  updated_at: string
}

export interface AgentPlaybookProjection {
  name: string
  display_name: string
  description: string
  tools: string[]
  instructions: string[]
  resource_key: string
  published_version: number
}

export interface AgentToolContract {
  name: string
  classification: 'read' | 'write' | 'system'
  risk_level: 'low' | 'medium' | 'high'
  confirmation_required: boolean
  controlled_action_required: boolean
  allowed_auto_execution_mode: string
  idempotency_key_strategy: string
  redaction_requirements: string[]
  input_schema: Record<string, unknown>
  executable: boolean
}

export interface AgentToolPolicy {
  id: number
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
  updated_at: string
}

export interface AgentIntegrationSummary {
  resource_id: number
  resource_key: string
  config_type: string
  published_version: number
  scope_rank: number
  kind: string
  credential_configured: boolean
  operations: Array<{
    key: string
    description: string
    mode: 'read' | 'write'
    method: string
    risk_level: string
    requires_confirmation: boolean
    enabled: boolean
  }>
}

export interface AgentControlSnapshot {
  generated_at: number
  tenant_key: string
  scope: {
    environment: 'test' | 'staging' | 'production'
    market_id?: number | null
    channel: string
    language?: string | null
    case_type?: string | null
  }
  definitions: AgentDefinition[]
  releases: AgentRelease[]
  deployments: AgentDeployment[]
  resolved_agent: Record<string, unknown>
  resolved_agent_digest: string
  personas: AgentPersona[]
  persona_total: number
  resources: AgentConfigResource[]
  resolved_playbooks: AgentPlaybookProjection[]
  tools: AgentToolContract[]
  tool_policies: AgentToolPolicy[]
  integrations: AgentIntegrationSummary[]
  capabilities: {
    can_manage: boolean
    can_deploy: boolean
    playground_model_execution: boolean
  }
}

export interface AgentPlaygroundResult {
  agent_release?: Record<string, unknown> | null
  agent_release_digest?: string | null
  persona?: Record<string, unknown> | null
  active_bulletins?: Array<Record<string, unknown>>
  playbooks: AgentPlaybookProjection[]
  tools: Array<Record<string, unknown>>
  model_executed: boolean
  reply?: string | null
  reply_source?: string | null
  intent?: string | null
  handoff_required?: boolean
  tool_calls?: Array<Record<string, unknown>>
  runtime_trace?: Record<string, unknown>
  error_code?: string | null
}
