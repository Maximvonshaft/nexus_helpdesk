export const marker = "'/ai-control'"
export const roleWorkspaceHint = '客服工作台'
export const canViewOps = true
export const queryMarker = 'enabled: !!session.data && canSeeOps'
export function AppShell() { return <main>{marker}{roleWorkspaceHint}{String(canViewOps)}{queryMarker}</main> }
