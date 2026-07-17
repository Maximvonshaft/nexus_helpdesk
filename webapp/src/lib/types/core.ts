export type BadgeTone = 'default' | 'warning' | 'success' | 'danger'
export interface AuthUser {
  id: number
  username: string
  display_name: string
  email?: string | null
  role: string
  team_id?: number | null
  capabilities?: string[]
}
export interface AdminUser extends AuthUser {
  is_active: boolean
  capabilities: string[]
  created_at: string
  updated_at: string
}
export interface SecurityCapabilityUser {
  user_id: number
  username: string
  display_name: string
  role: string
  is_active: boolean
  effective_capabilities: string[]
  override_count: number
  high_risk_count: number
}
export interface AdminAuditLog {
  id: number
  actor_id?: number | null
  actor_username?: string | null
  actor_display_name?: string | null
  action: string
  target_type: string
  target_id?: number | null
  old_value?: unknown
  new_value?: unknown
  created_at: string
}
export interface SecurityAuditSummary {
  total_users: number
  active_users: number
  inactive_users: number
  admin_users: number
  auditor_users: number
  high_risk_overrides: number
  recent_audit_24h: number
  catalog_size: number
  read_only: boolean
}
export interface SecurityAudit {
  capability_catalog: string[]
  users: SecurityCapabilityUser[]
  recent_audit: AdminAuditLog[]
  summary: SecurityAuditSummary
}
export interface Market {
  id: number
  code: string
  name: string
  country_code?: string | null
  language_code?: string | null
  timezone?: string | null
}
export interface Team {
  id: number
  name: string
  team_type: string
  market_id?: number | null
}
export interface LiteMeta {
  users: AuthUser[]
  teams: Team[]
  statuses: string[]
  priorities: string[]
}
export interface CaseListItem {
  id: number
  ticket_no?: string | null
  title: string
  status: string
  priority: string
  source_channel?: string | null
  category?: string | null
  sub_category?: string | null
  tracking_number?: string | null
  customer_name?: string | null
  assignee_name?: string | null
  team_name?: string | null
  market_id?: number | null
  market_code?: string | null
  country_code?: string | null
  conversation_state?: string | null
  updated_at: string
  resolution_due_at?: string | null
  overdue?: boolean
}
export interface CaseListPage {
  items: CaseListItem[]
  next_cursor: string | null
  has_more: boolean
  filters?: Record<string, unknown>
}
