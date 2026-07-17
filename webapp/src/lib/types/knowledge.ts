import type { BadgeTone } from './core'

export interface KnowledgeStudioKpi {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}
export interface KnowledgeStudioItem {
  id: number
  item_key: string
  title: string
  status: string
  source_type: string
  knowledge_kind: string
  channel?: string | null
  audience_scope: string
  language?: string | null
  priority: number
  parsing_status: string
  fact_status: string
  answer_mode: string
  published_version: number
  indexed_version: number
  chunk_count: number
  draft_ready: boolean
  publish_ready: boolean
  retrieval_test_ready: boolean
  has_conflict: boolean
  updated_at?: string | null
  href: string
  evidence: string
}
export interface KnowledgeStudioConflict {
  key: string
  term: string
  scope: string
  item_ids?: number[]
  item_keys: string[]
  titles: string[]
  status: string
  blocker: boolean
  href: string
  evidence?: string[]
}
export interface KnowledgeStudioLifecycleStep {
  key: string
  step: string
  owner: string
  artifact: string
  status: string
  count: number
  href: string
  enabled: boolean
}
export interface KnowledgeStudioTemplateBlock {
  key: string
  label: string
  backend_contract: string
  status: string
  evidence: string
  href: string
}
export interface KnowledgeStudio {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  kpis: KnowledgeStudioKpi[]
  items: KnowledgeStudioItem[]
  conflicts: KnowledgeStudioConflict[]
  release_lifecycle: KnowledgeStudioLifecycleStep[]
  template_blocks: KnowledgeStudioTemplateBlock[]
  facts: Record<string, number | string | boolean>
}
export interface PersonaBuilderKpi {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}
export interface PersonaBuilderProfile {
  id: number
  profile_key: string
  name: string
  description?: string | null
  market_id?: number | null
  channel?: string | null
  language?: string | null
  scope_label: string
  scope_specificity: number
  is_active: boolean
  published_version: number
  draft_ready: boolean
  published_ready: boolean
  needs_publish: boolean
  identity_ready: boolean
  boundary_ready: boolean
  guardrail_count: number
  risk_flags: string[]
  updated_at?: string | null
  href: string
  evidence: string
}
export interface PersonaBuilderReview {
  id: number
  profile_id: number
  profile_key?: string | null
  profile_name?: string | null
  review_version: number
  status: string
  summary?: string | null
  notes?: string | null
  scope_label: string
  requested_by?: number | null
  requested_at?: string | null
  reviewed_by?: number | null
  reviewed_at?: string | null
  decision_note?: string | null
  release_window_start?: string | null
  release_window_end?: string | null
  published_by?: number | null
  published_version?: number | null
  published_at?: string | null
  href: string
  evidence: string
}
export interface PersonaBuilderSimulationScenario {
  market_id?: number | null
  channel?: string | null
  language?: string | null
  matched_profile_key?: string | null
  matched_name?: string | null
  match_rank?: number | null
  published_version?: number | null
  reasons: string[]
  fallback: boolean
  status: string
  href: string
}
export interface PersonaBuilderLifecycleStep {
  key: string
  step: string
  owner: string
  artifact: string
  status: string
  count: number
  href: string
  enabled: boolean
}
export interface PersonaBuilderTemplateBlock {
  key: string
  label: string
  backend_contract: string
  status: string
  evidence: string
  href: string
}
export interface PersonaBuilder {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  kpis: PersonaBuilderKpi[]
  profiles: PersonaBuilderProfile[]
  approval_queue: PersonaBuilderReview[]
  simulation_scenarios: PersonaBuilderSimulationScenario[]
  release_lifecycle: PersonaBuilderLifecycleStep[]
  template_blocks: PersonaBuilderTemplateBlock[]
  facts: Record<string, number | string | boolean>
}
export interface KnowledgeItem {
  id: number
  item_key: string
  title: string
  summary?: string | null
  status: string
  source_type: string
  knowledge_kind: string
  market_id?: number | null
  channel?: string | null
  audience_scope: string
  language?: string | null
  priority: number
  starts_at?: string | null
  ends_at?: string | null
  source_url?: string | null
  file_name?: string | null
  file_storage_key?: string | null
  mime_type?: string | null
  file_size?: number | null
  parsing_status?: string | null
  parsing_error?: string | null
  parsed_at?: string | null
  indexed_version: number
  indexed_at?: string | null
  chunk_count: number
  fact_question?: string | null
  fact_answer?: string | null
  fact_aliases_json?: string[] | null
  fact_status?: string | null
  answer_mode?: string | null
  citation_metadata_json?: Record<string, unknown> | null
  draft_body?: string | null
  draft_normalized_text?: string | null
  published_body?: string | null
  published_normalized_text?: string | null
  published_version: number
  published_at?: string | null
  created_at: string
  updated_at: string
}
export interface KnowledgeItemVersion {
  id: number
  item_id: number
  version: number
  snapshot_json: Record<string, unknown>
  summary?: string | null
  notes?: string | null
  published_by?: number | null
  published_at: string
}
export interface KnowledgeItemDetail extends KnowledgeItem {
  versions: KnowledgeItemVersion[]
}
export interface KnowledgeItemList {
  items: KnowledgeItem[]
  total: number
}
export interface KnowledgeChunkHit {
  item_id: number
  item_key: string
  title: string
  published_version: number
  chunk_index: number
  score: number
  text: string
  retrieval_method?: string | null
  matched_terms?: string[]
  score_breakdown?: Record<string, number>
  direct_answer?: string | null
  answer_mode?: string | null
  source_metadata?: Record<string, unknown>
  metadata: Record<string, unknown>
}
export interface KnowledgeQueryAnalysis {
  language: string
  normalized_query: string
  entity_terms: string[]
  service_terms: string[]
  numeric_terms: string[]
  intent_terms: string[]
  terms: string[]
  high_value_terms: string[]
  fallback_ngrams: string[]
}
export interface KnowledgeRetrievalTestResult {
  hits: KnowledgeChunkHit[]
  total: number
  query_analysis?: KnowledgeQueryAnalysis | null
  candidate_count?: number
  top_hits?: Record<string, unknown>[]
  grounding_would_apply?: boolean
  grounding_source?: Record<string, unknown> | null
}
export interface KnowledgeConflictCheckResult {
  generated_at: string
  total: number
  conflicts: KnowledgeStudioConflict[]
  filters: Record<string, unknown>
}
export interface KnowledgeGoldenAssertion {
  key: string
  label: string
  passed: boolean
  expected?: string | null
  actual?: string | null
  evidence: string
}
export interface KnowledgeGoldenTestResult {
  generated_at: string
  passed: boolean
  query: string
  expected_item_key?: string | null
  assertions: KnowledgeGoldenAssertion[]
  retrieval: KnowledgeRetrievalTestResult
}
