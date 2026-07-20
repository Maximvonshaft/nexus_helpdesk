import { apiRequest, setSupportToken } from '@/lib/apiClient'
import type { AdminUser, SecurityAudit, Team } from '@/lib/types'
import type {
  AccountSecurity,
  AuthSessionResponse,
  RoleProfile,
  UserCreatePayload,
  UserSecurityState,
  UserUpdatePayload,
} from '@/lib/identityTypes'

type AdminUsersPage = {
  items: AdminUser[]
  next_cursor: string | null
  has_more: boolean
}

async function listAllAdminUsers() {
  const users: AdminUser[] = []
  const seenUserIds = new Set<number>()
  const seenCursors = new Set<string>()
  let cursor: string | null = null

  while (true) {
    const params = new URLSearchParams({ limit: '100', include_inactive: 'true' })
    if (cursor) params.set('cursor', cursor)
    const page = await apiRequest<AdminUsersPage>(`/api/admin/users?${params.toString()}`)

    for (const user of page.items) {
      if (seenUserIds.has(user.id)) continue
      seenUserIds.add(user.id)
      users.push(user)
    }

    if (!page.has_more || !page.next_cursor) break
    if (seenCursors.has(page.next_cursor)) throw new Error('用户分页游标未推进')
    seenCursors.add(page.next_cursor)
    cursor = page.next_cursor
  }

  return users
}

async function updateAdminUser(userId: number, payload: UserUpdatePayload) {
  const hasTeamAssignment = Object.prototype.hasOwnProperty.call(payload, 'team_id')
  const hasEmailAssignment = Object.prototype.hasOwnProperty.call(payload, 'email')
  const { team_id: teamId, email, ...profileFields } = payload
  const profilePayload = {
    ...profileFields,
    ...(hasEmailAssignment && email !== null ? { email } : {}),
  }

  let user = await apiRequest<AdminUser>(`/api/admin/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(profilePayload),
    requestIdPrefix: 'identity-admin',
  })

  if (hasEmailAssignment && email === null) {
    await apiRequest<{ ok: boolean; user_id: number; email: null }>(`/api/admin/users/${userId}/email`, {
      method: 'DELETE',
      requestIdPrefix: 'identity-admin',
    })
    user = { ...user, email: null }
  }

  if (hasTeamAssignment) {
    const assignment = await apiRequest<{ ok: boolean; user_id: number; team_id: number | null }>(`/api/admin/users/${userId}/team`, {
      method: 'PUT',
      body: JSON.stringify({ team_id: teamId ?? null }),
      requestIdPrefix: 'identity-admin',
    })
    user = { ...user, team_id: assignment.team_id }
  }

  return user
}

export const identityApi = {
  accountSecurity: () => apiRequest<AccountSecurity>('/api/auth/security'),

  changePassword: async (currentPassword: string, newPassword: string) => {
    const response = await apiRequest<AuthSessionResponse>('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      requestIdPrefix: 'account-security',
    })
    setSupportToken(response.access_token)
    return response
  },

  logoutAll: () => apiRequest<{ ok: boolean }>('/api/auth/logout-all', {
    method: 'POST',
    requestIdPrefix: 'account-security',
  }),

  users: listAllAdminUsers,
  teams: () => apiRequest<Team[]>('/api/lookups/teams'),
  roles: () => apiRequest<RoleProfile[]>('/api/admin/roles'),
  capabilityCatalog: () => apiRequest<string[]>('/api/admin/capabilities/catalog'),
  securityStates: () => apiRequest<UserSecurityState[]>('/api/admin/user-security-states'),
  securityAudit: () => apiRequest<SecurityAudit>('/api/admin/security-audit?limit=100'),

  createUser: (payload: UserCreatePayload) => apiRequest<AdminUser>('/api/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
    requestIdPrefix: 'identity-admin',
  }),

  updateUser: updateAdminUser,

  activateUser: (userId: number) => apiRequest<AdminUser>(`/api/admin/users/${userId}/activate`, {
    method: 'POST',
    requestIdPrefix: 'identity-admin',
  }),

  deactivateUser: (userId: number) => apiRequest<AdminUser>(`/api/admin/users/${userId}/deactivate`, {
    method: 'POST',
    requestIdPrefix: 'identity-admin',
  }),

  resetPassword: (userId: number, password: string) => apiRequest<{ ok: boolean }>(`/api/admin/users/${userId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ password }),
    requestIdPrefix: 'identity-admin',
  }),

  logoutUserEverywhere: (userId: number) => apiRequest<UserSecurityState>(`/api/admin/users/${userId}/logout-all`, {
    method: 'POST',
    requestIdPrefix: 'identity-admin',
  }),

  requirePasswordChange: (userId: number) => apiRequest<UserSecurityState>(`/api/admin/users/${userId}/require-password-change`, {
    method: 'POST',
    requestIdPrefix: 'identity-admin',
  }),
}
