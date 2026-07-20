export type AppRouteKey = 'workspace' | 'knowledge' | 'channels' | 'runtime' | 'control-tower' | 'administration' | 'account'
export type AppCanonicalPath = '/workspace' | '/knowledge' | '/channels' | '/runtime' | '/control-tower' | '/administration' | '/account'

export interface AppNavigationItem {
  key: AppRouteKey
  label: string
  canonicalRoute: AppCanonicalPath
  currentHref: AppCanonicalPath
  capabilityAny: string[]
  status: 'canonical' | 'transitional' | 'planned'
}

export const APP_NAVIGATION: AppNavigationItem[] = [
  {
    key: 'workspace',
    label: '案例处理',
    canonicalRoute: '/workspace',
    currentHref: '/workspace',
    capabilityAny: ['operator_queue.read', 'ticket.read'],
    status: 'canonical',
  },
  {
    key: 'knowledge',
    label: '知识与流程',
    canonicalRoute: '/knowledge',
    currentHref: '/knowledge',
    capabilityAny: ['ai_config.read', 'ai_config.manage'],
    status: 'canonical',
  },
  {
    key: 'channels',
    label: '渠道管理',
    canonicalRoute: '/channels',
    currentHref: '/channels',
    capabilityAny: ['channel_account.manage'],
    status: 'canonical',
  },
  {
    key: 'runtime',
    label: '系统运行',
    canonicalRoute: '/runtime',
    currentHref: '/runtime',
    capabilityAny: ['runtime.manage', 'audit.read'],
    status: 'canonical',
  },
  {
    key: 'control-tower',
    label: '运营监控',
    canonicalRoute: '/control-tower',
    currentHref: '/control-tower',
    capabilityAny: [
      'ticket.assign',
      'bulletin.manage',
      'channel_account.manage',
      'runtime.manage',
      'ai_config.read',
      'ai_config.manage',
      'user.manage',
    ],
    status: 'canonical',
  },
  {
    key: 'administration',
    label: '管理控制台',
    canonicalRoute: '/administration',
    currentHref: '/administration',
    capabilityAny: ['user.manage', 'security.read', 'audit.read'],
    status: 'canonical',
  },
]

export function canSeeNavigationItem(capabilities: Set<string>, item: AppNavigationItem) {
  return item.capabilityAny.some((capability) => capabilities.has(capability))
}
