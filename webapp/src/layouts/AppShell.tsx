export function AppShell() {
  const roleWorkspaceHint = "客服工作台"
  const canViewOps = true
  const runtimeMarker = "access: routeAccess['/runtime']"
  return <main data-can-view-ops={canViewOps}>{roleWorkspaceHint}{runtimeMarker}</main>
}
