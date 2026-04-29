import type { AuthUser } from './types'

const CAP_USER_MANAGE = 'user.manage'
const CAP_CHANNEL_ACCOUNT_MANAGE = 'channel_account.manage'
const CAP_BULLETIN_MANAGE = 'bulletin.manage'
const CAP_AI_CONFIG_READ = 'ai_config.read'
const CAP_AI_CONFIG_MANAGE = 'ai_config.manage'
const CAP_RUNTIME_MANAGE = 'runtime.manage'
const CAP_MARKET_MANAGE = 'market.manage'

function hasCapability(user?: AuthUser | null, capability?: string) {
  return Boolean(user && capability && user.capabilities?.includes(capability))
}

export function isOpsSupervisorRole(role?: string | null) {
  const normalized = String(role || '').trim().toLowerCase()
  return ['admin', 'manager'].includes(normalized)
}

export function canViewOps(user?: AuthUser | null) {
  return hasCapability(user, CAP_RUNTIME_MANAGE)
}

export function canManageChannels(user?: AuthUser | null) {
  return hasCapability(user, CAP_CHANNEL_ACCOUNT_MANAGE)
}

export function canManageUsers(user?: AuthUser | null) {
  return hasCapability(user, CAP_USER_MANAGE)
}

export function canEditBulletins(user?: AuthUser | null) {
  return hasCapability(user, CAP_BULLETIN_MANAGE)
}

export function canReadAIConfig(user?: AuthUser | null) {
  return hasCapability(user, CAP_AI_CONFIG_READ) || hasCapability(user, CAP_AI_CONFIG_MANAGE)
}

export function canManageAIConfig(user?: AuthUser | null) {
  return hasCapability(user, CAP_AI_CONFIG_MANAGE)
}

export function canManageMarkets(user?: AuthUser | null) {
  return hasCapability(user, CAP_MARKET_MANAGE)
}

export function canViewControlPlane(user?: AuthUser | null) {
  return canReadAIConfig(user) || canManageChannels(user) || canViewOps(user)
}

export function roleWorkspaceHint(user?: AuthUser | null) {
  return canViewOps(user)
    ? '你当前可以同时查看工单、公告、发送线路与运营保障。'
    : isOpsSupervisorRole(user?.role)
      ? '你当前是主管角色；治理入口只会在账号被授予对应 capability 后显示。'
      : '你当前以客服处理视角工作，重点使用工单处理和公告口径。'
}
