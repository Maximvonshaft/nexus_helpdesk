import type { AdminUser, AuthUser, SecurityAudit, Team } from '@/lib/types'

export type UserRole = 'admin' | 'manager' | 'lead' | 'agent' | 'auditor'

export interface AccountSecurity {
  user_id: number
  session_version: number
  must_change_password: boolean
  password_changed_at?: string | null
  last_login_at?: string | null
  updated_at?: string | null
}

export interface AuthSessionResponse {
  access_token: string
  token_type: string
  user: AuthUser
}

export type UserSecurityState = AccountSecurity

export interface RoleProfile {
  role: UserRole
  capabilities: string[]
}

export interface UserCreatePayload {
  username: string
  password: string
  display_name: string
  email?: string | null
  role: UserRole
  team_id?: number | null
  capabilities: string[]
}

export interface UserUpdatePayload {
  display_name?: string
  email?: string | null
  role?: UserRole
  team_id?: number | null
  capabilities?: string[]
}

export interface IdentityAdministrationSnapshot {
  users: AdminUser[]
  teams: Team[]
  roles: RoleProfile[]
  capabilityCatalog: string[]
  securityStates: UserSecurityState[]
  securityAudit: SecurityAudit
}
