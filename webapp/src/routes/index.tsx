import { useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, labelize, sanitizeDisplayText, severityTone } from '@/lib/format'
import { MetricCard } from '@/components/ui/MetricCard'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { Badge } from '@/components/ui/Badge'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Toast } from '@/components/ui/Toast'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useSession } from '@/hooks/useAuth'
import { canManageChannels, canViewOps } from '@/lib/access'

const closureSteps = [
  ['看到待办', '角色任务从真实后端计数进入首页，而不是靠客服记住每个模块。'],
  ['进入队列', '入口直接跳到 Workspace、WebChat、WebCall、Email 或运行恢复。'],
  ['执行动作', '分配、handoff、草稿保存、外发、恢复动作都走统一 API client。'],
  ['得到反馈', '刷新后重新拉取 view model，空态、错误和权限态都显式呈现。'],
  ['审计回写', '工单时间线、OutboundMessage、AdminAuditLog 或运行队列保留证据。'],
]

function OverviewPage() {
  const client = useQueryClient()
  const navigate = useNavigate()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const canSeeOps = canViewOps(session.data)
  const canSeeChannels = canManageChannels(session.data)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const workbench = useQuery({
    queryKey: ['todayWorkbench'],
    queryFn: api.todayWorkbench,
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })
  const queue = useQuery({
    queryKey: ['queueSummary'],
    queryFn: api.queueSummary,
    refetchInterval: autoRefresh.enabled ? 15000 : false,
    enabled: canSeeOps,
  })
  const runtime = useQuery({
    queryKey: ['runtimeHealth'],
    queryFn: api.runtimeHealth,
    refetchInterval: autoRefresh.enabled ? 15000 : false,
    enabled: canSeeOps,
  })
  const bulletins = useQuery({ queryKey: ['bulletins'], queryFn: api.bulletins, refetchInterval: autoRefresh.enabled ? 30000 : false })
  const accounts = useQuery({ queryKey: ['channelAccounts'], queryFn: api.channelAccounts, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: canSeeChannels })

  const q = queue.data
  const rt = runtime.data
  const runtimeRecoveryCount = (q?.dead_jobs ?? 0) + (q?.dead_outbound ?? 0) + (rt?.dead_sync_jobs ?? 0) + (rt?.dead_attachment_jobs ?? 0)
  const needsRuntimeRecovery = canSeeOps && runtimeRecoveryCount > 0
  const today = workbench.data

  return (
    <AppShell>
      <PageHeader
        eyebrow="BUSINESS OPERATIONS HOME"
        title="今日工作台 / 我的优先事项"
        description="按当前角色聚合真实待办、入口和交互状态；客服不用在 WebChat、WebCall、Email 和工单之间猜下一步。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={async () => { await client.invalidateQueries(); setToast({ message: '今日工作台已刷新', tone: 'success' }) }}>立即刷新</Button></div>}
      />

      <Card className="soft" data-testid="today-workbench-template-block">
        <CardHeader title={today ? `${today.role_label}优先事项` : '角色优先事项'} subtitle={today ? `生成时间 ${formatDateTime(today.generated_at)}` : '正在从后端聚合当前角色的真实任务。'} />
        <CardBody>
          {workbench.isError ? (
            <div className="message" data-role="agent">今日工作台加载失败，请刷新或确认当前账号是否拥有 ticket.read 权限。</div>
          ) : (
            <div className="message" data-role="assistant">{today?.mission ?? '正在读取角色任务、真实 API 来源和可见入口。'}</div>
          )}
        </CardBody>
      </Card>

      <div className="metrics-grid metrics-grid-wide">
        {(today?.metrics ?? []).map((metric) => (
          <MetricCard key={metric.key} label={metric.label} value={metric.value} hint={sanitizeDisplayText(metric.hint)} />
        ))}
        {workbench.isLoading ? <MetricCard label="正在加载" value="…" hint="等待 /api/today/workbench" /> : null}
      </div>

      <Card className="soft">
        <CardHeader title="角色任务" subtitle="v1.7.8 模板中的 role-task-card 现在由 /api/today/workbench 后端 view model 驱动。" />
        <CardBody>
          <div className="guide-grid">
            {(today?.tasks ?? []).map((task) => (
              <div className="guide-item role-task-card" key={task.key}>
                <div className="badges">
                  <Badge tone={severityTone(task.severity)}>{labelize(task.severity)}</Badge>
                  <Badge>{task.count}</Badge>
                </div>
                <strong>{task.title}</strong>
                <span>{sanitizeDisplayText(task.description)}</span>
                <span>来源：{sanitizeDisplayText(task.source)}</span>
                <Button variant={task.count > 0 ? 'primary' : 'secondary'} onClick={() => navigate({ to: task.route })}>{task.next}</Button>
              </div>
            ))}
            {workbench.isLoading ? <div className="guide-item role-task-card"><strong>正在加载任务</strong><span>后端正在计算当前账号可见范围。</span></div> : null}
            {!workbench.isLoading && !(today?.tasks.length) ? <div className="empty">当前没有角色任务。</div> : null}
          </div>
        </CardBody>
      </Card>

      <Card className="soft">
        <CardHeader title="角色任务闭环" subtitle="模板定义的工作顺序固定为看到待办、进入队列、执行动作、得到反馈、审计回写。" />
        <CardBody>
          <div className="guide-grid">
            {closureSteps.map(([title, body]) => (
              <div className="guide-item" key={title}>
                <strong>{title}</strong>
                <span>{body}</span>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>

      <Card className="soft">
        <CardHeader title="优先处理入口" subtitle="保留首页高频动作，同时让新工作台 view model 决定更细的角色入口。" />
        <CardBody>
          <div className="guide-grid" data-testid="overview-priority-actions">
            <div className="guide-item">
              <strong>处理客户工单</strong>
              <span>进入 Workspace，完成分配、回复、证据核对和处理结果保存。</span>
              <Button variant="primary" onClick={() => navigate({ to: '/workspace' })}>打开工单处理</Button>
            </div>
            <div className="guide-item">
              <strong>查看 WebChat 来信</strong>
              <span>处理网站实时会话、handoff、客户 action 和 WebChat 本地回复。</span>
              <Button variant="secondary" onClick={() => navigate({ to: '/webchat' })}>打开 WebChat 收件箱</Button>
            </div>
            {canSeeOps ? (
              <div className="guide-item">
                <strong>{needsRuntimeRecovery ? `运行恢复待处理 ${runtimeRecoveryCount}` : '运行恢复'}</strong>
                <span>{needsRuntimeRecovery ? '当前存在 dead/requeue 类异常，建议主管优先处理。' : '检查同步、队列、连接与恢复动作。'}</span>
                <Button variant={needsRuntimeRecovery ? 'primary' : 'secondary'} onClick={() => navigate({ to: '/runtime' })}>打开运行恢复</Button>
              </div>
            ) : (
              <div className="guide-item">
                <strong>需要主管协助</strong>
                <span>发送失败、同步异常、线路不可用时，备注清楚后交给主管处理。</span>
              </div>
            )}
          </div>
        </CardBody>
      </Card>

      <div className="page-grid split-grid">
        <Card>
          <CardHeader title="该角色可见入口" subtitle="入口不等同于权限放行；导航仍由 routeAccess 与后端 RBAC 双重约束。" />
          <CardBody>
            <DataTable
              columns={['入口', '说明', '真实来源', '动作']}
              rows={(today?.visible_entrypoints ?? []).map((entry) => [
                entry.label,
                sanitizeDisplayText(entry.hint),
                sanitizeDisplayText(entry.source),
                <Button key={entry.key} variant="secondary" onClick={() => navigate({ to: entry.route })}>打开</Button>,
              ])}
              loading={workbench.isLoading}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Command Center" subtitle="模板里的命令中心入口映射到现有生产接口和审计写回链路。" />
          <CardBody>
            <DataTable
              columns={['命令', '来源接口', '审计证据', '动作']}
              rows={(today?.command_center ?? []).map((command) => [
                command.label,
                sanitizeDisplayText(command.source),
                sanitizeDisplayText(command.audit),
                <Button key={command.key} variant="secondary" onClick={() => navigate({ to: command.route })}>打开</Button>,
              ])}
              loading={workbench.isLoading}
            />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="交互状态" subtitle="Loading、empty、error、permission denied、unsaved changes 都由同一个后端契约解释。" />
        <CardBody>
          <DataTable
            columns={['状态', '客服看到什么', '产品规则', '来源']}
            rows={(today?.interaction_states ?? []).map((state) => [
              state.state,
              sanitizeDisplayText(state.operator_signal),
              sanitizeDisplayText(state.product_rule),
              sanitizeDisplayText(state.source),
            ])}
            loading={workbench.isLoading}
          />
        </CardBody>
      </Card>

      <div className="page-grid split-grid">
        <Card>
          <CardHeader title="当前生效公告" subtitle="影响客服回复口径的公告与通知。" />
          <CardBody>
            <div className="list">
              {(bulletins.data ?? []).slice(0, 6).map((b) => (
                <div className="list-item" key={b.id}>
                  <div className="badges">
                    <Badge>{labelize(b.category || 'notice')}</Badge>
                    {b.severity ? <Badge tone={severityTone(b.severity)}>{labelize(b.severity)}</Badge> : null}
                    {b.auto_inject_to_ai ? <Badge tone="success">智能助手可引用</Badge> : null}
                  </div>
                  <div><strong>{sanitizeDisplayText(b.title)}</strong></div>
                  <div className="section-subtitle">{sanitizeDisplayText(b.summary || b.body)}</div>
                </div>
              ))}
              {!(bulletins.data?.length) ? <div className="empty">当前没有生效公告。</div> : null}
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="发送线路情况" subtitle="不同渠道的发送账号、健康度和兜底关系。" />
          <CardBody>
            {canSeeChannels ? (
              <DataTable
                columns={['渠道', '账号名称', '状态', '备用账号']}
                rows={(accounts.data ?? []).slice(0, 8).map((a) => [labelize(a.provider), sanitizeDisplayText(a.display_name || a.account_id), labelize(a.health_status), sanitizeDisplayText(a.fallback_account_id)])}
              />
            ) : (
              <div className="empty">你当前无需维护发送线路，异常时请通知主管处理。</div>
            )}
          </CardBody>
        </Card>
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: OverviewPage,
})
