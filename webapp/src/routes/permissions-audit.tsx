import { useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { AdminAuditLog, BadgeTone, PermissionsAuditUser } from '@/lib/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useSession } from '@/hooks/useAuth'
import { canAccess, capabilityMetadata, isHighRiskCapability, routeAccess } from '@/lib/rbac'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'

const EMPTY_AUDIT_USERS: PermissionsAuditUser[] = []

function groupCapabilities(capabilities: string[]) {
  const grouped = new Map<string, string[]>()
  for (const capability of capabilities) {
    const meta = capabilityMetadata(capability)
    grouped.set(meta.group, [...(grouped.get(meta.group) ?? []), capability])
  }
  return Array.from(grouped.entries())
    .map(([group, items]) => ({
      group,
      capabilities: items.sort((a, b) => capabilityMetadata(a).label.localeCompare(capabilityMetadata(b).label)),
    }))
    .sort((a, b) => a.group.localeCompare(b.group))
}

function changedKeys(log: AdminAuditLog) {
  const keys = new Set([...Object.keys(log.old_value ?? {}), ...Object.keys(log.new_value ?? {})])
  return [...keys].filter((key) => JSON.stringify(log.old_value?.[key]) !== JSON.stringify(log.new_value?.[key]))
}

function summarizeAuditValue(value: Record<string, unknown>) {
  const keys = Object.keys(value)
  if (!keys.length) return '—'
  return keys.slice(0, 4).map((key) => `${key}: ${sanitizeDisplayText(String(value[key] ?? 'null'))}`).join(' / ')
}

function userRoleTone(role: string): BadgeTone {
  if (role === 'admin') return 'danger'
  if (role === 'manager' || role === 'lead') return 'warning'
  if (role === 'auditor') return 'success'
  return 'default'
}

function capabilityBadges(capabilities: string[], emptyText: string) {
  if (!capabilities.length) return <span className="section-subtitle">{emptyText}</span>
  return (
    <div className="badges">
      {capabilities.map((capability) => {
        const meta = capabilityMetadata(capability)
        return <Badge key={capability} tone={isHighRiskCapability(capability) ? 'danger' : 'default'}>{meta.label}</Badge>
      })}
    </div>
  )
}

function PermissionsAuditContent() {
  const navigate = useNavigate()
  const session = useSession()
  const canManageUsers = canAccess(session.data, routeAccess['/users'])
  const query = useQuery({ queryKey: ['permissionsAudit'], queryFn: api.permissionsAudit })
  const data = query.data
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null)

  const users = useMemo(() => data?.users ?? EMPTY_AUDIT_USERS, [data?.users])
  const selectedUser = useMemo(() => {
    if (!users.length) return null
    return users.find((user) => user.id === selectedUserId) ?? users[0]
  }, [selectedUserId, users])

  const capabilityGroups = useMemo(() => groupCapabilities(data?.capability_catalog ?? []), [data?.capability_catalog])
  const auditRows = data?.audit_logs ?? []
  const readonlyAuditor = canAccess(session.data, routeAccess['/permissions-audit']) && !canManageUsers

  const baseCapabilities = new Set(selectedUser?.base_capabilities ?? [])
  const effectiveCapabilities = new Set(selectedUser?.effective_capabilities ?? [])
  const addedCapabilities = [...effectiveCapabilities].filter((capability) => !baseCapabilities.has(capability)).sort()
  const removedCapabilities = [...baseCapabilities].filter((capability) => !effectiveCapabilities.has(capability)).sort()

  return (
    <>
      <PageHeader
        eyebrow="系统配置"
        title="权限与审计"
        description="只读查看 capability 矩阵、用户授权差异和最近管理员审计记录。"
        actions={(
          <div className="button-row">
            {readonlyAuditor ? <Badge tone="success">Auditor readonly</Badge> : null}
            {canManageUsers ? <Button variant="secondary" onClick={() => navigate({ to: '/users' })}>进入账号权限</Button> : null}
            <Button variant="secondary" onClick={() => query.refetch()} disabled={query.isFetching}>{query.isFetching ? '刷新中...' : '刷新'}</Button>
          </div>
        )}
      />

      {query.isError ? (
        <Card>
          <CardHeader title="读取失败" subtitle="后端权限审计接口返回错误。" />
          <CardBody><div className="message" data-role="agent">{sanitizeDisplayText(query.error.message)}</div></CardBody>
        </Card>
      ) : null}

      <div className="metrics-grid">
        <MetricCard label="账号总数" value={data?.summary.total_users ?? '—'} />
        <MetricCard label="启用中" value={data?.summary.active_users ?? '—'} />
        <MetricCard label="Auditor" value={data?.summary.auditor_users ?? '—'} />
        <MetricCard label="高危覆盖" value={data?.summary.high_risk_override_count ?? '—'} hint={`最近审计 ${data?.summary.recent_audit_count ?? '—'} 条`} />
      </div>

      {readonlyAuditor ? (
        <Card className="soft">
          <CardHeader title="只读审计模式" subtitle="当前账号可以查看权限和审计差异，但不能创建、停用、重置密码或修改 capability override。" />
        </Card>
      ) : null}

      <div className="page-grid split-grid-wide" data-testid="permissions-audit-workbench">
        <Card>
          <CardHeader title="用户视角" subtitle="按角色、启用状态和 capability override 查看实际权限。" />
          <CardBody>
            {query.isLoading ? <Skeleton lines={8} /> : null}
            {!query.isLoading && !users.length ? <EmptyState text="暂无用户权限数据。" /> : null}
            {!query.isLoading && users.length ? (
              <div className="list">
                {users.map((user) => (
                  <button key={user.id} className={`queue-card ${selectedUser?.id === user.id ? 'selected' : ''}`} onClick={() => setSelectedUserId(user.id)}>
                    <div className="badges">
                      <Badge tone={userRoleTone(user.role)}>{labelize(user.role)}</Badge>
                      <Badge tone={user.is_active ? 'success' : 'danger'}>{user.is_active ? '启用中' : '已停用'}</Badge>
                      {user.overrides.length ? <Badge tone="warning">覆盖 {user.overrides.length}</Badge> : null}
                    </div>
                    <div className="queue-card-title">{sanitizeDisplayText(user.display_name)}</div>
                    <div className="queue-card-meta">@{sanitizeDisplayText(user.username)} · effective {user.effective_capabilities.length}</div>
                  </button>
                ))}
              </div>
            ) : null}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title={selectedUser ? `${sanitizeDisplayText(selectedUser.display_name)} 的授权差异` : '授权差异'} subtitle="base 来自角色，effective 为后端按 override 计算后的结果。" />
          <CardBody>
            {!selectedUser ? <EmptyState text="请选择用户。" /> : <UserLens user={selectedUser} addedCapabilities={addedCapabilities} removedCapabilities={removedCapabilities} />}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Capability Matrix" subtitle="能力目录按产品域分组，高危动作以红色标识。" />
        <CardBody>
          {query.isLoading ? <Skeleton lines={6} /> : (
            <DataTable
              columns={['分组', 'Capability']}
              rows={capabilityGroups.map((group) => [
                sanitizeDisplayText(group.group),
                capabilityBadges(group.capabilities, '无 capability'),
              ])}
            />
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="最近管理员审计" subtitle="展示 old/new 差异键，便于追踪权限、账号和运维动作来源。" />
        <CardBody>
          {query.isLoading ? <Skeleton lines={6} /> : (
            <DataTable
              columns={['时间', '操作者', '动作', '对象', '差异', '变更摘要']}
              rows={auditRows.map((log) => [
                formatDateTime(log.created_at),
                sanitizeDisplayText(log.actor_display_name || log.actor_username || log.actor_id || 'system'),
                sanitizeDisplayText(log.action),
                `${sanitizeDisplayText(log.target_type)} #${sanitizeDisplayText(log.target_id ?? '—')}`,
                changedKeys(log).join(', ') || '—',
                `${summarizeAuditValue(log.old_value)} -> ${summarizeAuditValue(log.new_value)}`,
              ])}
            />
          )}
        </CardBody>
      </Card>
    </>
  )
}

function UserLens({
  user,
  addedCapabilities,
  removedCapabilities,
}: {
  user: PermissionsAuditUser
  addedCapabilities: string[]
  removedCapabilities: string[]
}) {
  return (
    <div className="stack">
      <div className="badges">
        <Badge tone={userRoleTone(user.role)}>{labelize(user.role)}</Badge>
        <Badge tone={user.is_active ? 'success' : 'danger'}>{user.is_active ? '启用中' : '已停用'}</Badge>
        <Badge>{user.effective_capabilities.length} effective</Badge>
      </div>
      <DataTable
        columns={['项目', '值']}
        rows={[
          ['用户名', `@${sanitizeDisplayText(user.username)}`],
          ['邮箱', sanitizeDisplayText(user.email)],
          ['Team ID', sanitizeDisplayText(user.team_id)],
          ['Base capabilities', String(user.base_capabilities.length)],
          ['Effective capabilities', String(user.effective_capabilities.length)],
        ]}
      />
      <div className="stack compact">
        <div>
          <div className="section-title">新增能力</div>
          {capabilityBadges(addedCapabilities, '无新增 capability')}
        </div>
        <div>
          <div className="section-title">移除能力</div>
          {capabilityBadges(removedCapabilities, '无移除 capability')}
        </div>
        <div>
          <div className="section-title">Override 明细</div>
          <DataTable
            columns={['Capability', '状态', '更新时间']}
            rows={user.overrides.map((override) => {
              const meta = capabilityMetadata(override.capability)
              return [
                `${sanitizeDisplayText(meta.label)} · ${sanitizeDisplayText(override.capability)}`,
                <Badge tone={override.allowed ? 'warning' : 'danger'}>{override.allowed ? '允许' : '拒绝'}</Badge>,
                formatDateTime(override.updated_at),
              ]
            })}
          />
        </div>
      </div>
    </div>
  )
}

function PermissionsAuditPage() {
  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/permissions-audit']}>
        <PermissionsAuditContent />
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/permissions-audit',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: PermissionsAuditPage,
})
