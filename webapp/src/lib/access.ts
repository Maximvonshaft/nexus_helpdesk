import type { AuthUser } from './types'
import { CAPABILITIES, actionAccess, canAccess, hasCapability } from './rbac'

export { CAPABILITIES, actionAccess, canAccess, hasCapability } from './rbac'

export function isOpsSupervisorRole(role?: string | null) {
  const normalized = String(role || '').trim().toLowerCase()
  return ['admin', 'manager'].includes(normalized)
}

export function canViewOps(user?: AuthUser | null) {
  return hasCapability(user, CAPABILITIES.runtimeManage)
}

export function canManageChannels(user?: AuthUser | null) {
  return hasCapability(user, CAPABILITIES.channelAccountManage)
}

export function canManageUsers(user?: AuthUser | null) {
  return hasCapability(user, CAPABILITIES.userManage)
}

export function canEditBulletins(user?: AuthUser | null) {
  return hasCapability(user, CAPABILITIES.bulletinManage)
}

export function canReadAIConfig(user?: AuthUser | null) {
  return hasCapability(user, CAPABILITIES.aiConfigRead) || hasCapability(user, CAPABILITIES.aiConfigManage)
}

export function canManageAIConfig(user?: AuthUser | null) {
  return hasCapability(user, CAPABILITIES.aiConfigManage)
}

export function canManageMarkets(user?: AuthUser | null) {
  return hasCapability(user, CAPABILITIES.marketManage)
}

export function canViewControlPlane(user?: AuthUser | null) {
  return canReadAIConfig(user) || canManageChannels(user) || canViewOps(user)
}

export function canAssignTickets(user?: AuthUser | null) {
  return canAccess(user, actionAccess.assignTicket)
}

export function canUpdateTicketCore(user?: AuthUser | null) {
  return canAccess(user, actionAccess.updateTicketCore)
}

export function canChangeTicketStatus(user?: AuthUser | null) {
  return canAccess(user, actionAccess.changeTicketStatus)
}

export function canCloseTickets(user?: AuthUser | null) {
  return canAccess(user, actionAccess.closeTicket)
}

export function canWriteInternalNote(user?: AuthUser | null) {
  return canAccess(user, actionAccess.writeInternalNote)
}

export function canSendOutbound(user?: AuthUser | null) {
  return canAccess(user, actionAccess.sendOutbound)
}

export function canWriteAiIntake(user?: AuthUser | null) {
  return canAccess(user, actionAccess.writeAiIntake)
}

export function canCreateSpeedafWorkOrder(user?: AuthUser | null) {
  return canAccess(user, actionAccess.createSpeedafWorkOrder)
}

export function canUpdateSpeedafAddress(user?: AuthUser | null) {
  return canAccess(user, actionAccess.updateSpeedafAddress)
}

export function canCancelSpeedafOrder(user?: AuthUser | null) {
  return canAccess(user, actionAccess.cancelSpeedafOrder)
}

export function canReadWebcallVoice(user?: AuthUser | null) {
  return canAccess(user, actionAccess.readWebcallVoice)
}

export function canViewWebcallVoiceQueue(user?: AuthUser | null) {
  return canAccess(user, actionAccess.viewWebcallVoiceQueue)
}

export function canAcceptWebcallVoice(user?: AuthUser | null) {
  return canAccess(user, actionAccess.acceptWebcallVoice)
}

export function canRejectWebcallVoice(user?: AuthUser | null) {
  return canAccess(user, actionAccess.rejectWebcallVoice)
}

export function canEndWebcallVoice(user?: AuthUser | null) {
  return canAccess(user, actionAccess.endWebcallVoice)
}

export function canViewWebchatDebug(user?: AuthUser | null) {
  return canAccess(user, actionAccess.viewWebchatDebug)
}

export function canForceWebchatHandoff(user?: AuthUser | null) {
  return canAccess(user, actionAccess.forceWebchatHandoff)
}

export function canUploadAttachment(user?: AuthUser | null) {
  return canAccess(user, actionAccess.uploadAttachment)
}

export function canEscalateTickets(user?: AuthUser | null) {
  return canAccess(user, actionAccess.escalateTicket)
}

export function roleWorkspaceHint(user?: AuthUser | null) {
  return canViewOps(user)
    ? '你当前可以同时查看工单、公告、发送线路与运营保障。'
    : isOpsSupervisorRole(user?.role)
      ? '你当前是主管角色；治理入口只会在账号被授予对应 capability 后显示。'
      : '你当前以客服处理视角工作，重点使用工单处理和公告口径。'
}
