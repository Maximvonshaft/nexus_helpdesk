export type AgentConfigType = 'playbook' | 'integration' | 'model_profile' | 'runtime_policy' | 'memory_policy'

export interface AgentConfigResource {
  id: number
  resource_key: string
  config_type: AgentConfigType
  name: string
  description?: string | null
  scope_type: string
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
    method: string
    risk_level: string
    requires_confirmation: boolean
    enabled: boolean
  }>
}

export interface AgentControlSnapshot {
  generated_at: number
  tenant_key: string
  scope: { market_id?: number | null; channel: string; language?: string | null }
  personas: AgentPersona[]
  persona_total: number
  resources: AgentConfigResource[]
  resolved_playbooks: AgentPlaybookProjection[]
  tools: AgentToolContract[]
  tool_policies: AgentToolPolicy[]
  integrations: AgentIntegrationSummary[]
  memory_policy: Record<string, unknown>
  capabilities: { can_manage: boolean; playground_model_execution: boolean }
}

export interface AgentPlaygroundResult {
  persona?: Record<string, unknown> | null
  customer_memory?: Record<string, unknown> | null
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

export interface CustomerMemoryFact {
  id: number
  tenant_key: string
  customer_id: number
  memory_key: string
  value_text: string
  source_type: string
  source_reference?: string | null
  consent_basis?: string | null
  confidence: number
  sensitivity: string
  is_active: boolean
  expires_at?: string | null
  last_confirmed_at?: string | null
  created_at: string
  updated_at: string
}
