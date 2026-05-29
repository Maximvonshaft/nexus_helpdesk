import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { routeAccess } from '@/lib/rbac'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { MetricCard } from '@/components/ui/MetricCard'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { labelize, sanitizeDisplayText } from '@/lib/format'
import type { BadgeTone, ControlTowerAction } from '@/lib/types'

function safeTone(value: string | null | undefined): BadgeTone {
  return value === 'danger' || value === 'warning' || value === 'success' ? value : 'default'
}

function statusTone(value: string): BadgeTone {
  if (value === 'implemented') return 'success'
  if (value === 'linked') return 'warning'
  return 'default'
}

function ControlTowerPage() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh()
  const tower = useQuery({
    queryKey: ['controlTower'],
    queryFn: api.controlTower,
    refetchInterval: autoRefresh.enabled ? 30000 : false,
  })
  const actionMutation = useMutation({
    mutationFn: api.submitControlTowerAction,
    onSuccess: async () => {
      await refresh()
    },
  })

  const goTarget = (href: string) => {
    if (href === '/workspace') navigate({ to: '/workspace' })
    else if (href === '/webchat') navigate({ to: '/webchat' })
    else if (href === '/webcall') navigate({ to: '/webcall' })
    else if (href === '/email') navigate({ to: '/email' })
    else if (href === '/bulletins') navigate({ to: '/bulletins' })
    else if (href === '/runtime') navigate({ to: '/runtime' })
    else if (href === '/accounts') navigate({ to: '/accounts' })
    else if (href === '/outbound-email') navigate({ to: '/outbound-email' })
    else if (href === '/ai-control') navigate({ to: '/ai-control' })
    else if (href === '/users') navigate({ to: '/users' })
    else navigate({ to: '/control-tower' })
  }

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['controlTower'] }),
      client.invalidateQueries({ queryKey: ['todayWorkbench'] }),
      client.invalidateQueries({ queryKey: ['queueSummary'] }),
      client.invalidateQueries({ queryKey: ['runtimeHealth'] }),
    ])
  }

  const submitAction = (action: ControlTowerAction) => {
    actionMutation.mutate({
      action_key: action.key,
      label: action.label,
      href: action.href,
      count: action.count,
      note: action.next,
    })
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="Control Tower"
        title="Control Tower / Governance Console"
        description="主管用一个页面看队列负载、SLA 风险、公告影响、渠道健康、权限覆盖和运行异常。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={() => void refresh()} disabled={tower.isFetching}>刷新</Button></div>}
      />

      <RequireCapability requirement={routeAccess['/control-tower']}>
        {tower.isLoading ? <Skeleton lines={6} /> : null}
        {tower.isError ? <ErrorSummary title="Control Tower 加载失败" errors={[tower.error instanceof Error ? tower.error.message : '请稍后重试']} action={<Button variant="secondary" onClick={() => void refresh()}>重试</Button>} /> : null}
        {actionMutation.isError ? <ErrorSummary title="治理任务创建失败" errors={[actionMutation.error instanceof Error ? actionMutation.error.message : '请检查权限或稍后重试']} /> : null}
        {tower.data ? (
          <div className="stack" data-testid="control-tower-template-blocks">
            <div className="metrics-grid metrics-grid-wide" data-testid="control-tower-real-kpis">
              {tower.data.kpis.map((item) => (
                <div className="stack" key={item.key}>
                  <MetricCard label={item.label} value={item.value} hint={item.hint} />
                  <Badge tone={safeTone(item.tone)}>{labelize(item.tone)}</Badge>
                </div>
              ))}
            </div>

            <Card className="soft" data-testid="control-tower-manager-actions">
              <CardHeader title="主管动作队列" subtitle="每个动作都带 capability、目标页和当前风险计数。" />
              <CardBody>
                <div className="guide-grid">
                  {tower.data.manager_actions.map((action: ControlTowerAction) => (
                    <div className="guide-item" key={action.key}>
                      <div className="badges"><Badge tone={safeTone(action.tone)}>{action.count}</Badge><Badge>{sanitizeDisplayText(action.capability)}</Badge></div>
                      <strong>{sanitizeDisplayText(action.label)}</strong>
                      <span>{sanitizeDisplayText(action.next)}</span>
                      {action.action_status ? <Badge tone="warning">{sanitizeDisplayText(action.action_status)}</Badge> : null}
                      <div className="button-row">
                        <Button
                          data-testid="control-tower-action-command"
                          variant={action.tone === 'danger' || action.tone === 'warning' ? 'primary' : 'secondary'}
                          disabled={!action.enabled || actionMutation.isPending || Boolean(action.action_task_id)}
                          onClick={() => submitAction(action)}
                        >
                          {action.action_task_id ? '已建任务' : action.enabled ? '创建任务' : '当前角色不可用'}
                        </Button>
                        <Button variant="secondary" disabled={!action.enabled} onClick={() => goTarget(action.href)}>打开处理</Button>
                      </div>
                    </div>
                  ))}
                </div>
              </CardBody>
            </Card>

            <div className="page-grid split-grid-wide">
              <Card data-testid="control-tower-team-workload">
                <CardHeader title="队列负载 / Team Workload" subtitle="从当前账号可见工单聚合团队负载和 SLA 风险。" />
                <CardBody>
                  <DataTable
                    columns={['团队', '活动工单', '未分配', 'SLA 风险', '已超时']}
                    rows={tower.data.team_workload.map((item) => [
                      sanitizeDisplayText(item.team_name),
                      String(item.active_tickets),
                      <Badge tone={item.unassigned ? 'warning' : 'success'}>{item.unassigned}</Badge>,
                      <Badge tone={safeTone(item.sla_risk >= 3 ? 'danger' : item.sla_risk ? 'warning' : 'success')}>{item.sla_risk}</Badge>,
                      <Badge tone={safeTone(item.overdue ? 'danger' : 'success')}>{item.overdue}</Badge>,
                    ])}
                    empty={<EmptyState title="暂无队列负载" description="当前可见范围内没有活动工单。" />}
                  />
                </CardBody>
              </Card>

              <Card data-testid="control-tower-channel-health">
                <CardHeader title="渠道健康 / Channel Health" subtitle="WebChat、WebCall、Email 和 Runtime 统一看风险与入口。" />
                <CardBody>
                  <DataTable
                    columns={['渠道', '健康', '队列量', '风险', '入口']}
                    rows={tower.data.channel_health.map((item) => [
                      sanitizeDisplayText(item.label),
                      <Badge tone={safeTone(item.health)}>{labelize(item.health)}</Badge>,
                      String(item.queue),
                      <Badge tone={safeTone(item.risk ? 'warning' : 'success')}>{item.risk}</Badge>,
                      <Button variant="secondary" disabled={!item.enabled} onClick={() => goTarget(item.href)}>{item.enabled ? '打开' : '无权限'}</Button>,
                    ])}
                  />
                </CardBody>
              </Card>
            </div>

            <div className="page-grid split-grid-wide">
              <Card data-testid="control-tower-bulletin-impact">
                <CardHeader title="公告影响" subtitle="按当前生效公告的 severity 和 category 聚合。" />
                <CardBody>
                  <DataTable
                    columns={['Severity', 'Category', '数量', '状态']}
                    rows={tower.data.bulletin_impact.map((item) => [
                      sanitizeDisplayText(item.severity),
                      sanitizeDisplayText(item.category),
                      String(item.count),
                      <Badge tone={safeTone(item.tone)}>{labelize(item.tone)}</Badge>,
                    ])}
                    empty={<EmptyState title="当前没有生效公告" description="客服回复口径不受公告变更影响。" />}
                  />
                </CardBody>
              </Card>

              <Card data-testid="control-tower-governance-lanes">
                <CardHeader title="治理泳道" subtitle="RBAC、渠道、AI、审计和运行安全聚合到同一个管理视图。" />
                <CardBody>
                  <DataTable
                    columns={['区域', '指标', '风险', '下一步', '入口']}
                    rows={tower.data.governance_lanes.map((item) => [
                      sanitizeDisplayText(item.area),
                      String(item.value),
                      <Badge tone={safeTone(item.risk)}>{labelize(item.risk)}</Badge>,
                      sanitizeDisplayText(item.next),
                      <Button variant="secondary" disabled={!item.enabled} onClick={() => goTarget(item.href)}>{item.enabled ? '打开' : '无权限'}</Button>,
                    ])}
                  />
                </CardBody>
              </Card>
            </div>

            <Card data-testid="control-tower-template-closure">
              <CardHeader title="v1.7.8 模板块落地状态" subtitle="只展示已经接入真实后端契约或明确链接到现有受控页面的块。" />
              <CardBody>
                <DataTable
                  columns={['模板块', '后端契约', '状态', '证据', '入口']}
                  rows={tower.data.template_blocks.map((item) => [
                    sanitizeDisplayText(item.label),
                    sanitizeDisplayText(item.backend_contract),
                    <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>,
                    sanitizeDisplayText(item.evidence),
                    <Button variant="secondary" onClick={() => goTarget(item.href)}>查看</Button>,
                  ])}
                />
              </CardBody>
            </Card>
          </div>
        ) : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/control-tower',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: ControlTowerPage,
})
