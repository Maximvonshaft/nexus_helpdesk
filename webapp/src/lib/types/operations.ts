import type { BadgeTone } from './core'

export interface TodayWorkbenchTask {
  key: string
  title: string
  count: number | string
  severity: BadgeTone
  source: string
  next: string
  target: string
  href: string
  enabled: boolean
}
export interface TodayWorkbenchMetric {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}
export interface TodayWorkbenchSlaPriority {
  ticket_id: number
  ticket_no?: string | null
  title: string
  priority: string
  status: string
  source_channel?: string | null
  customer_name?: string | null
  assignee_name?: string | null
  team_name?: string | null
  resolution_due_at?: string | null
  first_response_due_at?: string | null
  minutes_to_due?: number | null
  overdue: boolean
  href: string
}
export interface TodayWorkbenchInteractionState {
  key: string
  state: string
  user_copy: string
  required: string
  status: string
}
export interface TodayWorkbenchCommand {
  key: string
  label: string
  role: string
  target: string
  href: string
  next: string
  enabled: boolean
  capability: string
}
export interface TodayWorkbench {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  tasks: TodayWorkbenchTask[]
  metrics: TodayWorkbenchMetric[]
  sla_priorities: TodayWorkbenchSlaPriority[]
  interaction_states: TodayWorkbenchInteractionState[]
  command_center: TodayWorkbenchCommand[]
}
export interface ControlTowerKpi {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}
export interface ControlTowerAction {
  key: string
  label: string
  count: number
  tone: BadgeTone
  next: string
  href: string
  capability: string
  enabled: boolean
  action_task_id?: number | null
  action_status?: string | null
}
export interface ControlTowerTeamWorkload {
  team_id?: number | null
  team_name: string
  active_tickets: number
  unassigned: number
  sla_risk: number
  overdue: number
}
export interface ControlTowerChannelHealth {
  key: string
  label: string
  health: BadgeTone
  queue: number
  risk: number
  href: string
  capability: string
  enabled: boolean
}
export interface ControlTowerBulletinImpact {
  severity: string
  category: string
  count: number
  tone: BadgeTone
}
export interface ControlTowerGovernanceLane {
  key: string
  area: string
  value: number
  risk: BadgeTone
  next: string
  href: string
  capability: string
  enabled: boolean
}
export interface ControlTowerTemplateBlock {
  key: string
  label: string
  backend_contract: string
  status: string
  evidence: string
  href: string
}
export interface ControlTower {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  kpis: ControlTowerKpi[]
  manager_actions: ControlTowerAction[]
  team_workload: ControlTowerTeamWorkload[]
  channel_health: ControlTowerChannelHealth[]
  bulletin_impact: ControlTowerBulletinImpact[]
  governance_lanes: ControlTowerGovernanceLane[]
  template_blocks: ControlTowerTemplateBlock[]
  facts: Record<string, number | string | string[]>
}
export interface ControlTowerActionResult {
  ok: boolean
  task_id: number
  created: boolean
  status: string
  action_key: string
  submitted_at: string
}
export interface QATrainingKpi {
  key: string
  label: string
  value: number
  hint: string
  tone: BadgeTone
}
export interface QATrainingQueueItem {
  key: string
  channel: string
  sample: string
  ticket_id: number
  ticket_no?: string | null
  customer_name?: string | null
  agent_name?: string | null
  ai_pre_score: number
  risk: string
  feedback: string
  agent_appeal: string
  appeal_status?: string | null
  appeal_task_id?: number | null
  source: string
  created_at?: string | null
  href: string
  evidence: string[]
}
export interface QATrainingScorecardRow {
  key: string
  criterion: string
  score: number
  tone: BadgeTone
  evidence: string
  next: string
}
export interface QATrainingTask {
  key: string
  title: string
  owner: string
  priority: number
  status: string
  source: string
  next: string
  href: string
  enabled: boolean
  capability: string
}
export interface QATrainingKnowledgeGap {
  key: string
  title: string
  source: string
  status: string
  owner: string
  next: string
  href: string
  evidence: string
  resource_id?: number | null
  ticket_id?: number | null
  sample_key?: string | null
  channel?: string | null
  sample?: string | null
}
export interface QATrainingLoopStep {
  key: string
  step: string
  owner: string
  artifact: string
  status: string
  href: string
  enabled: boolean
}
export interface QATrainingTemplateBlock {
  key: string
  label: string
  backend_contract: string
  status: string
  evidence: string
  href: string
}
export interface QATraining {
  generated_at: string
  role: string
  user_id: number
  capabilities: string[]
  kpis: QATrainingKpi[]
  qa_queue: QATrainingQueueItem[]
  scorecard: QATrainingScorecardRow[]
  training_tasks: QATrainingTask[]
  knowledge_gaps: QATrainingKnowledgeGap[]
  loop_steps: QATrainingLoopStep[]
  template_blocks: QATrainingTemplateBlock[]
  facts: Record<string, number | string | boolean>
}
export interface QATrainingAppealResult {
  ok: boolean
  task_id: number
  created: boolean
  status: string
  ticket_id: number
  sample_key: string
  appeal_status: string
  submitted_at: string
}
export interface QATrainingKnowledgeGapResult {
  ok: boolean
  resource_id: number
  resource_key: string
  task_id: number
  created: boolean
  status: string
  ticket_id?: number | null
  gap_key: string
  submitted_at: string
}
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
