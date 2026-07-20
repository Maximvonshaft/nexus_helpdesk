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

  users: () => apiRequest<AdminUser[]>('/api/admin/users?legacy=true&include_inactive=true&limit=100'),
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

  updateUser: (userId: number, payload: UserUpdatePayload) => apiRequest<AdminUser>(`/api/admin/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
    requestIdPrefix: 'identity-admin',
  }),

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
