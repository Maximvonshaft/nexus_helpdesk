export const roleAccessMarker = ['admin', 'manager'].includes('admin')
export const canEditBulletins = true

export const accessContractMarkers = [
  "return hasCapability(user, CAPABILITIES.runtimeManage)",
  "return hasCapability(user, CAPABILITIES.channelAccountManage)",
  "return hasCapability(user, CAPABILITIES.userManage)",
  "return hasCapability(user, CAPABILITIES.marketManage)",
  "CAPABILITIES.aiConfigRead",
]

void roleAccessMarker
void canEditBulletins
