import { useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { Field, Input, Select } from '@/components/ui/Field'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { RequireCapability } from '@/components/security/RequireCapability'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { capabilityMetadata, routeAccess } from '@/lib/rbac'
import type { SecurityAuditLogEntry, SecurityAuditRisk, SecurityAuditUserLens } from '@/lib/types'

function riskTone(risk: SecurityAuditRisk): 'success' | 'warning' | 'danger' {
  if (risk === 'high') return 'danger'
  if (risk === 'medium') return 'warning'
  return 'success'
}

function RiskBadge({ risk }: { risk: SecurityAuditRisk }) {
  return <Badge tone={riskTone(risk)}>{risk}</Badge>
}

function capabilityChips(capabilities: string[], max = 4) {
  const shown = capabilities.slice(0, max)
  return (
    <div className="badges">
      {shown.map((capability) => <Badge key={capability} tone={capabilityMetadata(capability).risk === 'high' ? 'danger' : 'default'}>{capability}</Badge>)}
      {capabilities.length > max ? <Badge tone="warning">+{capabilities.length - max}</Badge> : null}
    </div>
  )
}

function jsonPreview(value: unknown) {
  if (value == null) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function userMatches(user: SecurityAuditUserLens, query: string, role: string, risk: string) {
  if (role !== 'all' && user.role !== role) return false
  if (risk !== 'all' && user.risk !== risk) return false
  if (!query) return true
  const haystack = [
    user.username,
    user.display_name,
    user.email,
    user.role,
    ...user.capabilities,
  ].join(' ').toLowerCase()
  return haystack.includes(query)
}

function auditRows(entries: SecurityAuditLogEntry[]) {
  return entries.map((entry) => [
    <div className="stack compact">
      <strong>{sanitizeDisplayText(entry.action)}</strong>
      <span className="section-subtitle">{formatDateTime(entry.created_at)}</span>
    </div>,
    sanitizeDisplayText(entry.actor_display_name || `user#${entry.actor_id ?? 'system'}`),
    <div className="stack compact">
      <span>{sanitizeDisplayText(entry.target_type)} #{entry.target_id ?? '—'}</span>
      <div className="badges">{entry.changed_fields.map((field) => <Badge key={field}>{field}</Badge>)}</div>
    </div>,
    <RiskBadge risk={entry.risk} />,
    <pre className="json-preview">{jsonPreview(entry.old_value)}</pre>,
    <pre className="json-preview">{jsonPreview(entry.new_value)}</pre>,
  ])
}

function SecurityAuditPageContent() {
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('all')
  const [riskFilter, setRiskFilter] = useState('all')
  const normalizedSearch = search.trim().toLowerCase()
  const auditQuery = useQuery({
    queryKey: ['securityAudit', normalizedSearch],
    queryFn: () => api.securityAudit({ limit: 50, q: normalizedSearch || undefined }),
    refetchInterval: 30000,
  })
  const data = auditQuery.data
  const readonly = !data?.contracts.can_manage_users

  const filteredUsers = useMemo(
    () => (data?.users ?? []).filter((user) => userMatches(user, normalizedSearch, roleFilter, riskFilter)),
    [data?.users, normalizedSearch, roleFilter, riskFilter],
  )

  const matrixRows = useMemo(() => (data?.capability_matrix ?? []).map((row) => [
    <div className="stack compact">
      <strong>{capabilityMetadata(row.capability).label}</strong>
      <span className="section-subtitle">{row.capability}</span>
    </div>,
    sanitizeDisplayText(row.group),
    <RiskBadge risk={row.risk} />,
    ...['agent', 'lead', 'manager', 'admin', 'auditor'].map((role) => <Badge tone={row.roles_allowed[role] ? 'success' : 'default'}>{row.roles_allowed[role] ? 'allow' : 'deny'}</Badge>),
  ]), [data?.capability_matrix])

  const userRows = filteredUsers.map((user) => [
    <div className="stack compact">
      <strong>{sanitizeDisplayText(user.display_name)}</strong>
      <span className="section-subtitle">@{sanitizeDisplayText(user.username)}{user.email ? ` · ${sanitizeDisplayText(user.email)}` : ''}</span>
    </div>,
    <Badge tone={user.role === 'admin' ? 'danger' : user.role === 'auditor' ? 'warning' : 'default'}>{labelize(user.role)}</Badge>,
    <RiskBadge risk={user.risk} />,
    capabilityChips(user.high_risk_capabilities.length ? user.high_risk_capabilities : user.capabilities),
    `${user.allow_override_count}/${user.deny_override_count}`,
    formatDateTime(user.last_capability_change_at),
  ])

  return (
    <>
      <PageHeader
        eyebrow="Security & RBAC"
        title="权限与审计矩阵"
        description="Capability、用户权限、审计 diff 的只读治理视图。"
        actions={<div className="button-row"><Badge tone={readonly ? 'warning' : 'success'}>{readonly ? '审计员只读' : '管理员可写在账号页'}</Badge><Button variant="secondary" onClick={() => auditQuery.refetch()} disabled={auditQuery.isFetching}>{auditQuery.isFetching ? '刷新中…' : '刷新'}</Button></div>}
      />

      <div className="metrics-grid">
        <MetricCard label="Capability" value={data?.summary.capability_count ?? '—'} hint={`高危 ${data?.summary.high_risk_capability_count ?? '—'}`} />
        <MetricCard label="账号" value={data?.summary.user_count ?? '—'} hint={`启用 ${data?.summary.active_user_count ?? '—'}`} />
        <MetricCard label="Admin / Auditor" value={`${data?.summary.admin_user_count ?? '—'} / ${data?.summary.auditor_user_count ?? '—'}`} />
        <MetricCard label="审计记录" value={data?.summary.recent_audit_count ?? '—'} hint={`override ${data?.summary.override_count ?? '—'}`} />
      </div>

      <Card className="soft">
        <CardBody>
          <div className="form-grid three">
            <Field label="搜索"><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="用户、capability、audit action" /></Field>
            <Field label="角色"><Select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}><option value="all">全部角色</option><option value="agent">Agent</option><option value="lead">Lead</option><option value="manager">Manager</option><option value="admin">Admin</option><option value="auditor">Auditor</option></Select></Field>
            <Field label="风险"><Select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)}><option value="all">全部风险</option><option value="high">high</option><option value="medium">medium</option><option value="low">low</option></Select></Field>
          </div>
        </CardBody>
      </Card>

      <div className="page-grid split-grid">
        <Card>
          <CardHeader title="Capability Matrix" subtitle={`required: ${(data?.contracts.required_capabilities ?? []).join(' + ') || '—'}`} />
          <CardBody><div className="table-wrap"><DataTable columns={['Capability', 'Group', 'Risk', 'Agent', 'Lead', 'Manager', 'Admin', 'Auditor']} rows={matrixRows} loading={auditQuery.isLoading} /></div></CardBody>
        </Card>
        <Card>
          <CardHeader title="Role Matrix" subtitle="后端 ROLE_CAPABILITIES 事实。" />
          <CardBody>
            <div className="table-wrap">
              <DataTable
                columns={['Role', 'Capability', 'High risk']}
                rows={(data?.role_matrix ?? []).map((role) => [
                  <Badge tone={role.role === 'admin' ? 'danger' : role.role === 'auditor' ? 'warning' : 'default'}>{labelize(role.role)}</Badge>,
                  `${role.capability_count}`,
                  capabilityChips(role.high_risk_capabilities, 3),
                ])}
                loading={auditQuery.isLoading}
              />
            </div>
          </CardBody>
        </Card>
      </div>

      <div className="page-grid split-grid-wide">
        <Card>
          <CardHeader title="用户访问列表 / User Lens" subtitle="按真实 effective capabilities 和 override 计算。" />
          <CardBody><div className="table-wrap"><DataTable columns={['User', 'Role', 'Risk', 'Capability', 'Allow/Deny', 'Last change']} rows={userRows} loading={auditQuery.isLoading} /></div></CardBody>
        </Card>
        <Card>
          <CardHeader title="权限变更 Diff / Audit Trail" subtitle={data?.contracts.secret_values_exposed ? '存在明文风险' : 'secret/token/password 已脱敏'} />
          <CardBody><div className="table-wrap"><DataTable columns={['Action', 'Actor', 'Target / Fields', 'Risk', 'Old', 'New']} rows={auditRows(data?.audit_logs ?? [])} loading={auditQuery.isLoading} /></div></CardBody>
        </Card>
      </div>
    </>
  )
}

function SecurityAuditPage() {
  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/security-audit']}>
        <SecurityAuditPageContent />
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/security-audit',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: SecurityAuditPage,
})
