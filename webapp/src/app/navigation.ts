export type AppRouteKey = 'workspace' | 'knowledge' | 'channels' | 'runtime' | 'control-tower'

export interface AppNavigationItem {
  key: AppRouteKey
  label: string
  canonicalRoute: string
  currentHref: string
  capabilityAny: string[]
  status: 'canonical' | 'transitional' | 'planned'
}

export const APP_NAVIGATION: AppNavigationItem[] = [
  {
    key: 'workspace',
    label: '工作台',
    canonicalRoute: '/workspace',
    currentHref: '/workspace',
    capabilityAny: ['operator_queue.read', 'ticket.read'],
    status: 'canonical',
  },
  {
    key: 'knowledge',
    label: '知识',
    canonicalRoute: '/knowledge',
    currentHref: '/webchat?tab=knowledge',
    capabilityAny: ['ai_config.read', 'ai_config.manage'],
    status: 'transitional',
  },
  {
    key: 'channels',
    label: '渠道',
    canonicalRoute: '/channels',
    currentHref: '/channels',
    capabilityAny: ['channel_account.manage'],
    status: 'canonical',
  },
  {
    key: 'runtime',
    label: '运行与审计',
    canonicalRoute: '/runtime',
    currentHref: '/runtime',
    capabilityAny: ['runtime.manage', 'audit.read'],
    status: 'canonical',
  },
  {
    key: 'control-tower',
    label: '运营总览',
    canonicalRoute: '/control-tower',
    currentHref: '/control-tower',
    capabilityAny: ['control_tower.read'],
    status: 'planned',
  },
]

export function canSeeNavigationItem(capabilities: Set<string>, item: AppNavigationItem) {
  return item.capabilityAny.some((capability) => capabilities.has(capability))
}
