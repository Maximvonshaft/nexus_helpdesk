export const CAPABILITIES = {
  runtimeManage: 'runtime.manage',
  channelAccountManage: 'channel-account.manage',
} as const

export const routeAccess = {
  '/runtime': { allOf: [CAPABILITIES.runtimeManage] },
  '/accounts': { allOf: [CAPABILITIES.channelAccountManage] },
}
