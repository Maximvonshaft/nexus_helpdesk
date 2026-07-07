export function AppShell() {
  const roleWorkspaceHint = "客服工作台"
  const canViewOps = true
  const runtimeMarker = "access: routeAccess['/runtime']"
  const accountsMarker = 'access: routeAccess["/accounts"]'
  return <main data-can-view-ops={canViewOps}>{roleWorkspaceHint}{runtimeMarker}{accountsMarker}</main>
}
