export type BadgeTone = 'default' | 'warning' | 'success' | 'danger'
export interface AuthUser {
  id: number
  username: string
  display_name: string
  email?: string | null
  role: string
  team_id?: number | null
  capabilities?: string[]
  must_change_password?: boolean
  password_changed_at?: string | null
  last_login_at?: string | null
  mfa_enabled?: boolean
}
export interface AuthSessionResponse {
  access_token: string
  token_type?: string
  user: AuthUser
}
export interface MfaLoginChallenge {
  mfa_required: true
  challenge_token: string
  expires_in_seconds: number
  display_name: string
}
export type LoginResult = AuthSessionResponse | MfaLoginChallenge
export interface MfaStatus {
  enabled: boolean
  setup_pending: boolean
  confirmed_at?: string | null
  last_verified_at?: string | null
  recovery_codes_remaining: number
}
export interface MfaSetupBegin {
  secret: string
  otpauth_uri: string
}
export interface MfaRecoveryCodes {
  ok: boolean
  recovery_codes: string[]
  reauthenticate: boolean
}
export interface AdminUser extends AuthUser {
  is_active: boolean
  capabilities: string[]
  created_at: string | null
  updated_at: string | null
}
export interface AdminUserPage {
  items: AdminUser[]
  next_cursor: string | null
  has_more: boolean
  filters: {
    limit: number
    include_inactive: boolean
  }
}
export interface CredentialPolicy {
  user_id: number
  username: string
  display_name: string
  role: string
  is_active: boolean
  must_change_password: boolean
  password_changed_at?: string | null
  last_login_at?: string | null
  mfa_enabled: boolean
  mfa_confirmed_at?: string | null
  mfa_last_verified_at?: string | null
  mfa_recovery_codes_remaining: number
  updated_at?: string | null
}
export interface RolePolicy {
  role: string
  default_capabilities: string[]
}
export interface AdminUserCreate {
  username: string
  password: string
  display_name: string
  email?: string | null
  role: string
  team_id?: number | null
  capabilities: string[]
}
export interface AdminUserUpdate {
  display_name?: string
  email?: string | null
  role?: string
  team_id?: number | null
  capabilities?: string[]
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
export interface IdentityTeam extends Team {
  is_active: boolean
  active_users: number
  created_at: string
  updated_at: string
}
export interface IdentityTeamCreate {
  name: string
  team_type: string
  market_id?: number | null
}
export interface IdentityTeamUpdate {
  name?: string
  team_type?: string
  market_id?: number | null
  is_active?: boolean
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
