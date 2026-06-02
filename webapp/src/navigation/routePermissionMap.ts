import type { AccessRequirement } from '@/lib/rbac'

export const ROUTE_CAPABILITIES = {
  ticketRead: 'ticket.read',
  ticketAssign: 'ticket.assign',
  customerProfileRead: 'customer_profile.read',
  outboundDraftSave: 'outbound.draft.save',
  outboundSend: 'outbound.send',
  channelAccountManage: 'channel_account.manage',
  aiConfigRead: 'ai_config.read',
  aiConfigManage: 'ai_config.manage',
  runtimeManage: 'runtime.manage',
  qaManage: 'qa.manage',
  userManage: 'user.manage',
  securityRead: 'security.read',
  auditRead: 'audit.read',
  webcallVoiceQueueView: 'webcall.voice.queue.view',
  webchatHandoffAccept: 'webchat.handoff.accept',
  webchatHandoffForceTakeover: 'webchat.handoff.force_takeover',
} as const

export const routePermissionMap = {
  '/': {},
  '/workspace': { allOf: [ROUTE_CAPABILITIES.ticketRead] },
  '/webchat': { anyOf: [ROUTE_CAPABILITIES.ticketRead, ROUTE_CAPABILITIES.webchatHandoffAccept, ROUTE_CAPABILITIES.webchatHandoffForceTakeover] },
  '/webcall': { allOf: [ROUTE_CAPABILITIES.webcallVoiceQueueView] },
  '/email': { allOf: [ROUTE_CAPABILITIES.ticketRead], anyOf: [ROUTE_CAPABILITIES.outboundDraftSave, ROUTE_CAPABILITIES.outboundSend] },
  '/customer-waybill': { allOf: [ROUTE_CAPABILITIES.customerProfileRead] },
  '/control-tower': { anyOf: [ROUTE_CAPABILITIES.ticketAssign, ROUTE_CAPABILITIES.channelAccountManage, ROUTE_CAPABILITIES.runtimeManage, ROUTE_CAPABILITIES.aiConfigRead, ROUTE_CAPABILITIES.aiConfigManage, ROUTE_CAPABILITIES.userManage] },
  '/qa-training': { allOf: [ROUTE_CAPABILITIES.qaManage] },
  '/runtime': { allOf: [ROUTE_CAPABILITIES.runtimeManage] },
  '/accounts': { allOf: [ROUTE_CAPABILITIES.channelAccountManage] },
  '/outbound-email': { allOf: [ROUTE_CAPABILITIES.channelAccountManage] },
  '/provider-credentials': { allOf: [ROUTE_CAPABILITIES.runtimeManage] },
  '/bulletins': {},
  '/ai-control': { allOf: [ROUTE_CAPABILITIES.aiConfigManage] },
  '/knowledge-studio': { anyOf: [ROUTE_CAPABILITIES.aiConfigRead, ROUTE_CAPABILITIES.aiConfigManage] },
  '/persona-builder': { anyOf: [ROUTE_CAPABILITIES.aiConfigRead, ROUTE_CAPABILITIES.aiConfigManage] },
  '/control-plane': { anyOf: [ROUTE_CAPABILITIES.aiConfigRead, ROUTE_CAPABILITIES.aiConfigManage, ROUTE_CAPABILITIES.channelAccountManage, ROUTE_CAPABILITIES.runtimeManage] },
  '/users': { allOf: [ROUTE_CAPABILITIES.userManage] },
  '/security': { anyOf: [ROUTE_CAPABILITIES.userManage, ROUTE_CAPABILITIES.securityRead, ROUTE_CAPABILITIES.auditRead] },
  '/webcall-ai': { anyOf: [ROUTE_CAPABILITIES.webcallVoiceQueueView, ROUTE_CAPABILITIES.runtimeManage] },
  '/webcall-ai-demo': { allOf: [ROUTE_CAPABILITIES.runtimeManage] },
  '/webchat-voice': { allOf: [ROUTE_CAPABILITIES.webcallVoiceQueueView] },
} satisfies Record<string, AccessRequirement>

export type RoutePermissionPath = keyof typeof routePermissionMap

export const operationsOnlyRoutes = [
  '/runtime',
  '/provider-credentials',
  '/outbound-email',
  '/users',
  '/security',
  '/control-plane',
  '/ai-control',
  '/webcall-ai-demo',
  '/accounts',
] as const

function normalizePathname(pathname: string) {
  const pathOnly = pathname.split('?')[0]?.split('#')[0] || '/'
  if (pathOnly.length > 1) return pathOnly.replace(/\/+$/, '')
  return pathOnly
}

export function routeRequirementFor(pathname: string): AccessRequirement | undefined {
  const normalized = normalizePathname(pathname)

  // Customer-side voice rooms are public/deep-link pages. The operator console is exactly /webcall.
  if (normalized.startsWith('/webcall/') && normalized !== '/webcall') return undefined

  const direct = routePermissionMap[normalized as RoutePermissionPath]
  if (direct) return direct

  const matched = Object.keys(routePermissionMap)
    .filter((route) => route !== '/' && normalized.startsWith(`${route}/`))
    .sort((a, b) => b.length - a.length)[0] as RoutePermissionPath | undefined

  return matched ? routePermissionMap[matched] : undefined
}
