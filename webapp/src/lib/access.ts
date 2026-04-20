import type { AuthUser } from './types'

const CAP_USER_MANAGE = 'user.manage'

function hasCapability(user?: AuthUser | null, capability?: string) {
  return Boolean(user && capability && user.capabilities?.includes(capability))
}

export function isOpsSupervisorRole(role?: string | null) {
  const normalized = String(role || '').trim().toLowerCase()
  return ['admin', 'manager'].includes(normalized)
}

export function canViewOps(role?: string | null) {
  return isOpsSupervisorRole(role)
}

export function canManageChannels(role?: string | null) {
  return isOpsSupervisorRole(role)
}

export function canManageUsers(user?: AuthUser | null) {
  return isOpsSupervisorRole(user?.role) || hasCapability(user, CAP_USER_MANAGE)
}

export function canEditBulletins(user?: AuthUser | null) {
  if (user?.capabilities?.includes('bulletin.edit') !== undefined) {
    // If backend formally exposes bulletin.edit in the future, we could use it.
    // Right now capability list doesn't have bulletin.edit but let's prep for capability-first.
    // However, the backend schemas.py currently manages ticket/attachment caps.
    // We'll just stick to the supervisor rule for now as the main fallback.
  }
  return isOpsSupervisorRole(user?.role)
}

export function roleWorkspaceHint(role?: string | null) {
  return canViewOps(role)
    ? '你当前可以同时查看工单、公告、发送线路与运营保障。'
    : '你当前以客服处理视角工作，重点使用工单处理和公告口径。'
}

export function canManageAIConfig(role?: string | null) {
  return isOpsSupervisorRole(role)
}
