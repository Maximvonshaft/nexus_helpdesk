import type { KnowledgeStudioConflict } from './operations'

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
export interface ChannelOnboardingTask {
  id: number
  provider: string
  status: string
  requested_by?: number | null
  market_id?: number | null
  target_slot?: string | null
  desired_display_name?: string | null
  desired_channel_account_binding?: string | null
  external_channel_account_id?: string | null
  last_error?: string | null
  created_at: string
  updated_at: string
  started_at?: string | null
  completed_at?: string | null
}
export interface ChannelOnboardingTaskList {
  tasks: ChannelOnboardingTask[]
  total: number
}
export interface ExternalChannelUnresolvedEvent {
  id: number
  source: string
  session_key?: string | null
  event_type?: string | null
  recipient?: string | null
  source_chat_id?: string | null
  preferred_reply_contact?: string | null
  status: string
  replay_count: number
  last_error?: string | null
  created_at: string
  updated_at: string
}
