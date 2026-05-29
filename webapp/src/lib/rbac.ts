import type { AuthUser } from './types'

export const CAPABILITIES = {
  ticketRead: 'ticket.read',
  ticketAssign: 'ticket.assign',
  ticketEscalate: 'ticket.escalate',
  ticketUpdateCore: 'ticket.update_core',
  ticketStatusChange: 'ticket.status.change',
  ticketClose: 'ticket.close',
  attachmentReadExternal: 'attachment.read.external',
  attachmentReadInternal: 'attachment.read.internal',
  attachmentUpload: 'attachment.upload',
  customerProfileRead: 'customer_profile.read',
  outboundDraftSave: 'outbound.draft.save',
  outboundSend: 'outbound.send',
  aiIntakeWrite: 'ai_intake.write',
  noteWriteInternal: 'note.write.internal',
  noteWriteExternal: 'note.write.external',
  userManage: 'user.manage',
  channelAccountManage: 'channel_account.manage',
  bulletinManage: 'bulletin.manage',
  aiConfigRead: 'ai_config.read',
  aiConfigManage: 'ai_config.manage',
  runtimeManage: 'runtime.manage',
  marketManage: 'market.manage',
  speedafWorkOrderWrite: 'tool:speedaf.work_order.create:write',
  speedafAddressUpdateWrite: 'tool:speedaf.order.update_address:write',
  speedafCancelWrite: 'tool:speedaf.order.cancel:write',
  webcallVoiceRead: 'webcall.voice.read',
  webcallVoiceQueueView: 'webcall.voice.queue.view',
  webcallVoiceAccept: 'webcall.voice.accept',
  webcallVoiceReject: 'webcall.voice.reject',
  webcallVoiceEnd: 'webcall.voice.end',
  webchatHandoffForceTakeover: 'webchat.handoff.force_takeover',
} as const

export type Capability = (typeof CAPABILITIES)[keyof typeof CAPABILITIES]

export type AccessRequirement = {
  allOf?: string[]
  anyOf?: string[]
}

export type CapabilityMeta = {
  capability: string
  label: string
  group: string
  description: string
  risk: 'normal' | 'high'
}

export const routeAccess = {
  '/runtime': { allOf: [CAPABILITIES.runtimeManage] },
  '/integration': { allOf: [CAPABILITIES.runtimeManage] },
  '/provider-credentials': { allOf: [CAPABILITIES.runtimeManage] },
  '/webcall': { allOf: [CAPABILITIES.webcallVoiceQueueView] },
  '/webcall-ai-demo': { allOf: [CAPABILITIES.runtimeManage] },
  '/accounts': { allOf: [CAPABILITIES.channelAccountManage] },
  '/outbound-email': { allOf: [CAPABILITIES.channelAccountManage] },
  '/email': { allOf: [CAPABILITIES.ticketRead], anyOf: [CAPABILITIES.outboundDraftSave, CAPABILITIES.outboundSend] },
  '/ai-control': { allOf: [CAPABILITIES.aiConfigManage] },
  '/control-plane': { anyOf: [CAPABILITIES.aiConfigRead, CAPABILITIES.aiConfigManage, CAPABILITIES.channelAccountManage, CAPABILITIES.runtimeManage] },
  '/users': { allOf: [CAPABILITIES.userManage] },
  '/webchat-voice': { allOf: [CAPABILITIES.webcallVoiceQueueView] },
} satisfies Record<string, AccessRequirement>

export const actionAccess = {
  assignTicket: { allOf: [CAPABILITIES.ticketAssign] },
  updateTicketCore: { allOf: [CAPABILITIES.ticketUpdateCore] },
  changeTicketStatus: { allOf: [CAPABILITIES.ticketStatusChange] },
  closeTicket: { allOf: [CAPABILITIES.ticketClose] },
  writeInternalNote: { allOf: [CAPABILITIES.noteWriteInternal] },
  sendOutbound: { allOf: [CAPABILITIES.outboundSend] },
  writeAiIntake: { allOf: [CAPABILITIES.aiIntakeWrite] },
  createSpeedafWorkOrder: { allOf: [CAPABILITIES.speedafWorkOrderWrite] },
  updateSpeedafAddress: { allOf: [CAPABILITIES.speedafAddressUpdateWrite] },
  cancelSpeedafOrder: { allOf: [CAPABILITIES.speedafCancelWrite] },
  readWebcallVoice: { allOf: [CAPABILITIES.webcallVoiceRead] },
  viewWebcallVoiceQueue: { allOf: [CAPABILITIES.webcallVoiceQueueView] },
  acceptWebcallVoice: { allOf: [CAPABILITIES.webcallVoiceAccept] },
  rejectWebcallVoice: { allOf: [CAPABILITIES.webcallVoiceReject] },
  endWebcallVoice: { allOf: [CAPABILITIES.webcallVoiceEnd] },
  viewWebchatDebug: { anyOf: [CAPABILITIES.runtimeManage] },
  forceWebchatHandoff: { allOf: [CAPABILITIES.webchatHandoffForceTakeover] },
  uploadAttachment: { allOf: [CAPABILITIES.attachmentUpload] },
  escalateTicket: { allOf: [CAPABILITIES.ticketEscalate] },
} satisfies Record<string, AccessRequirement>

export const capabilityCatalogMeta: CapabilityMeta[] = [
  { capability: CAPABILITIES.ticketRead, label: '查看工单', group: '工单处理', description: '查看可见范围内的工单、时间线和证据。', risk: 'normal' },
  { capability: CAPABILITIES.ticketAssign, label: '分配工单', group: '工单处理', description: '修改工单负责人或团队。', risk: 'high' },
  { capability: CAPABILITIES.ticketEscalate, label: '升级工单', group: '工单处理', description: '把工单升级到主管处理。', risk: 'high' },
  { capability: CAPABILITIES.ticketUpdateCore, label: '编辑核心字段', group: '工单处理', description: '修改下一步、缺失资料、客户更新和解决摘要。', risk: 'normal' },
  { capability: CAPABILITIES.ticketStatusChange, label: '变更状态', group: '工单处理', description: '推进普通工单状态。', risk: 'normal' },
  { capability: CAPABILITIES.ticketClose, label: '关闭/取消工单', group: '工单处理', description: '执行关闭、取消、升级等终态或高影响状态。', risk: 'high' },
  { capability: CAPABILITIES.attachmentReadExternal, label: '查看客户附件', group: '附件与客户资料', description: '查看客户侧附件和证据。', risk: 'normal' },
  { capability: CAPABILITIES.attachmentReadInternal, label: '查看内部附件', group: '附件与客户资料', description: '查看内部可见附件。', risk: 'normal' },
  { capability: CAPABILITIES.attachmentUpload, label: '上传附件', group: '附件与客户资料', description: '向工单补充附件。', risk: 'normal' },
  { capability: CAPABILITIES.customerProfileRead, label: '查看客户资料', group: '附件与客户资料', description: '查看客户联系方式和基本资料。', risk: 'normal' },
  { capability: CAPABILITIES.outboundDraftSave, label: '保存回复草稿', group: '客户沟通', description: '保存客户回复草稿。', risk: 'normal' },
  { capability: CAPABILITIES.outboundSend, label: '发送客户回复', group: '客户沟通', description: '发送或记录客户回复。', risk: 'high' },
  { capability: CAPABILITIES.noteWriteInternal, label: '写内部备注', group: '客户沟通', description: '写入内部处理备注。', risk: 'normal' },
  { capability: CAPABILITIES.noteWriteExternal, label: '写外部评论', group: '客户沟通', description: '写入客户可见评论。', risk: 'high' },
  { capability: CAPABILITIES.aiIntakeWrite, label: '保存智能提炼', group: 'AI 辅助', description: '写入 AI intake 或结构化提炼结果。', risk: 'normal' },
  { capability: CAPABILITIES.aiConfigRead, label: '查看 AI 配置', group: '治理配置', description: '查看 AI 配置和发布状态。', risk: 'normal' },
  { capability: CAPABILITIES.aiConfigManage, label: '管理 AI 配置', group: '治理配置', description: '发布、回滚、停用 AI 规则和知识配置。', risk: 'high' },
  { capability: CAPABILITIES.bulletinManage, label: '管理公告口径', group: '治理配置', description: '新增、编辑、停用客服公告口径。', risk: 'high' },
  { capability: CAPABILITIES.channelAccountManage, label: '管理发送线路', group: '治理配置', description: '维护渠道账号、兜底线路和健康状态。', risk: 'high' },
  { capability: CAPABILITIES.runtimeManage, label: '运行恢复', group: '治理配置', description: '执行重排、同步、连接检查等运维动作。', risk: 'high' },
  { capability: CAPABILITIES.marketManage, label: '管理市场', group: '治理配置', description: '维护市场和团队归属。', risk: 'high' },
  { capability: CAPABILITIES.userManage, label: '管理员工账号', group: '账号权限', description: '创建账号、停用账号、重置密码和授权。', risk: 'high' },
  { capability: CAPABILITIES.speedafWorkOrderWrite, label: 'Speedaf 催派工单', group: 'Speedaf 工具', description: '创建 Speedaf 派送跟进工单。', risk: 'high' },
  { capability: CAPABILITIES.speedafAddressUpdateWrite, label: 'Speedaf 地址更新', group: 'Speedaf 工具', description: '提交地址更新确认流程。', risk: 'high' },
  { capability: CAPABILITIES.speedafCancelWrite, label: 'Speedaf 取消运单', group: 'Speedaf 工具', description: '执行取消运单预检和确认提交。', risk: 'high' },
  { capability: CAPABILITIES.webcallVoiceRead, label: '查看 WebCall 语音', group: 'WebCall 语音', description: '查看所选工单的语音会话和历史证据。', risk: 'normal' },
  { capability: CAPABILITIES.webcallVoiceQueueView, label: '查看来电队列', group: 'WebCall 语音', description: '查看全局来电队列和活动通话。', risk: 'normal' },
  { capability: CAPABILITIES.webcallVoiceAccept, label: '接听 WebCall', group: 'WebCall 语音', description: '接听客户发起的 WebCall。', risk: 'high' },
  { capability: CAPABILITIES.webcallVoiceReject, label: '拒接 WebCall', group: 'WebCall 语音', description: '拒接客户发起的 WebCall。', risk: 'high' },
  { capability: CAPABILITIES.webcallVoiceEnd, label: '结束 WebCall', group: 'WebCall 语音', description: '挂断或结束语音会话。', risk: 'high' },
  { capability: CAPABILITIES.webchatHandoffForceTakeover, label: '强制接管 WebChat AI', group: 'WebChat 接管', description: '在 AI 正在处理时强制暂停 AI 并接管会话。', risk: 'high' },
]

const metaByCapability = new Map(capabilityCatalogMeta.map((item) => [item.capability, item]))

export function hasCapability(user?: AuthUser | null, capability?: string) {
  return Boolean(user && capability && user.capabilities?.includes(capability))
}

export function hasAllCapabilities(user: AuthUser | null | undefined, capabilities: string[] = []) {
  return capabilities.every((capability) => hasCapability(user, capability))
}

export function hasAnyCapability(user: AuthUser | null | undefined, capabilities: string[] = []) {
  return capabilities.length === 0 || capabilities.some((capability) => hasCapability(user, capability))
}

export function canAccess(user: AuthUser | null | undefined, requirement?: AccessRequirement) {
  if (!requirement) return true
  if (requirement.allOf?.length && !hasAllCapabilities(user, requirement.allOf)) return false
  if (requirement.anyOf?.length && !hasAnyCapability(user, requirement.anyOf)) return false
  return true
}

export function capabilityMetadata(capability: string): CapabilityMeta {
  return metaByCapability.get(capability) ?? {
    capability,
    label: capability,
    group: '其他权限',
    description: '后端权限目录中的扩展 capability。',
    risk: 'normal',
  }
}

export function isHighRiskCapability(capability: string) {
  return capabilityMetadata(capability).risk === 'high'
}
