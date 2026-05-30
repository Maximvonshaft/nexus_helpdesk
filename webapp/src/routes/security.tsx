import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { BadgeTone, SecurityCapabilityUser } from '@/lib/types'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { MetricCard } from '@/components/ui/MetricCard'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useSession } from '@/hooks/useAuth'
import { canAccess, capabilityMetadata, routeAccess } from '@/lib/rbac'

function roleTone(role: string): BadgeTone {
  if (role === 'admin') return 'danger'
  if (role === 'manager') return 'warning'
  if (role === 'auditor') return 'success'
  return 'default'
}

function payloadPreview(value: unknown) {
  if (value === null || value === undefined) return '无'
  const raw = typeof value === 'string' ? value : JSON.stringify(value)
  return sanitizeDisplayText(raw.length > 180 ? `${raw.slice(0, 180)}...` : raw)
}

function capabilityPreview(user: SecurityCapabilityUser) {
  const capabilities = user.effective_capabilities ?? []
  if (!capabilities.length) return <span>无</span>
  return (
    <div className="badges" data-testid={`security-user-capabilities-${user.user_id}`}>
      {capabilities.slice(0, 4).map((capability) => {
        const meta = capabilityMetadata(capability)
        return <Badge key={capability} tone={meta.risk === 'high' ? 'danger' : 'default'}>{sanitizeDisplayText(meta.label)}</Badge>
      })}
      {capabilities.length > 4 ? <Badge>+{capabilities.length - 4}</Badge> : null}
    </div>
  )
}

function SecurityPageContent() {
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const navigate = useNavigate()
  const security = useQuery({
    queryKey: ['securityAudit'],
    queryFn: () => api.securityAudit({ limit: 40 }),
    refetchInterval: autoRefresh.enabled ? 30000 : false,
  })
  const data = security.data
  const summary = data?.summary
  const canManageUsers = canAccess(session.data, routeAccess['/users'])

  return (
    <>
      <PageHeader
        eyebrow="权限与审计"
        title="Security & Audit Lens"
        description="只读查看真实 capability 目录、账号权限矩阵和最近管理员审计记录。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button>{canManageUsers ? <Button onClick={() => navigate({ to: '/users' })}>编辑账号权限</Button> : null}</div>}
      />

      <div className="metrics-grid metrics-grid-wide" data-testid="security-audit-lens">
        <MetricCard label="账号总数" value={summary?.total_users ?? '...'} hint={`启用 ${summary?.active_users ?? 0} / 停用 ${summary?.inactive_users ?? 0}`} />
        <MetricCard label="Admin" value={summary?.admin_users ?? '...'} hint="具备完整治理权限" />
        <MetricCard label="Auditor" value={summary?.auditor_users ?? '...'} hint="默认只读审计入口" />
        <MetricCard label="24h 审计" value={summary?.recent_audit_24h ?? '...'} hint={`${summary?.catalog_size ?? 0} 个 capability`} />
        <MetricCard label="高危覆盖" value={summary?.high_risk_overrides ?? '...'} hint={summary?.read_only ? '当前账号只读' : '当前账号可管理用户'} />
      </div>

      {security.isError ? <Card><CardBody><div className="message" data-role="agent">{sanitizeDisplayText((security.error as Error).message)}</div></CardBody></Card> : null}

      <div className="page-grid split-grid-wide">
        <Card>
          <CardHeader title="Capability Matrix" subtitle="每个账号的最终生效权限来自角色基线加用户级 override。" />
          <CardBody>
            <div data-testid="security-capability-matrix">
              <DataTable
                loading={security.isLoading}
                columns={['账号', '角色', '状态', '权限', '覆盖', '高危']}
                rows={(data?.users ?? []).map((user) => [
                  <div key="account"><strong>{sanitizeDisplayText(user.display_name)}</strong><div className="section-subtitle">@{sanitizeDisplayText(user.username)}</div></div>,
                  <Badge key="role" tone={roleTone(user.role)}>{labelize(user.role)}</Badge>,
                  <Badge key="status" tone={user.is_active ? 'success' : 'danger'}>{user.is_active ? '启用' : '停用'}</Badge>,
                  capabilityPreview(user),
                  String(user.override_count),
                  user.high_risk_count ? <Badge key="risk" tone="danger">{user.high_risk_count}</Badge> : <Badge key="risk">0</Badge>,
                ])}
              />
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Recent Admin Audit" subtitle="后端返回的审计 diff 已脱敏 password、token、secret 和 credential 字段。" />
          <CardBody>
            <div className="list" data-testid="security-recent-audit">
              {security.isLoading ? <div className="message">正在加载...</div> : null}
              {!security.isLoading && !(data?.recent_audit ?? []).length ? <EmptyState text="暂无管理员审计记录。" /> : null}
              {(data?.recent_audit ?? []).map((item) => (
                <div key={item.id} className="queue-card">
                  <div className="badges">
                    <Badge tone="warning">{sanitizeDisplayText(item.action)}</Badge>
                    <Badge>{sanitizeDisplayText(item.target_type)}#{item.target_id ?? 'n/a'}</Badge>
                  </div>
                  <div className="queue-card-title">{sanitizeDisplayText(item.actor_display_name || item.actor_username || 'system')} · {formatDateTime(item.created_at)}</div>
                  <div className="queue-card-meta">old: {payloadPreview(item.old_value)}</div>
                  <div className="queue-card-meta">new: {payloadPreview(item.new_value)}</div>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      </div>
    </>
  )
}

function SecurityPage() {
  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/security']}>
        <SecurityPageContent />
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/security',
  component: SecurityPage,
  beforeLoad: () => {
    if (!getToken()) throw redirect({ to: '/login' })
  },
})
