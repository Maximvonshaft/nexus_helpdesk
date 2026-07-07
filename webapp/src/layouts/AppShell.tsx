export function AppShell() {
  const roleWorkspaceHint = "客服工作台"
  const canViewOps = true
  return <main data-can-view-ops={canViewOps}>{roleWorkspaceHint}</main>
}
