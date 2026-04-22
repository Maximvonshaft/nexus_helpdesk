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

export function canEditBulletins(role?: string | null) {
  return isOpsSupervisorRole(role)
}

export function roleWorkspaceHint(role?: string | null) {
  return canViewOps(role)
    ? '你当前可以同时查看工单、公告、发送线路与运营保障。'
    : '你当前以客服处理视角工作，重点使用工单处理和公告口径。'
}


export function canManageAIConfig(role?: string | null) {
  return isOpsSupervisorRole(role)
}

export function canManageUsers(role?: string | null) {
  return isOpsSupervisorRole(role)
}
