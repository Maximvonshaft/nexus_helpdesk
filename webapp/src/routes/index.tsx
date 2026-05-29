import { useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, labelize, priorityTone, sanitizeDisplayText, severityTone, signoffLabel, statusTone } from '@/lib/format'
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
import type { BadgeTone, TodayWorkbenchTask } from '@/lib/types'

function taskTone(severity?: string): BadgeTone {
  const normalized = String(severity || '').toLowerCase()
  if (normalized === 'danger') return 'danger'
  if (normalized === 'warning' || normalized === 'processing') return 'warning'
  if (normalized === 'success') return 'success'
  return 'default'
}

function OverviewPage() {
  const client = useQueryClient()
  const navigate = useNavigate()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const canSeeOps = canViewOps(session.data)
  const canSeeChannels = canManageChannels(session.data)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const today = useQuery({ queryKey: ['todayWorkbench'], queryFn: api.todayWorkbench, refetchInterval: autoRefresh.enabled ? 15000 : false })
  const [queue, runtime, readiness, signoff] = useQueries({
    queries: [
      { queryKey: ['queueSummary'], queryFn: api.queueSummary, refetchInterval: autoRefresh.enabled ? 15000 : false, enabled: canSeeOps },
      { queryKey: ['runtimeHealth'], queryFn: api.runtimeHealth, refetchInterval: autoRefresh.enabled ? 15000 : false, enabled: canSeeOps },
      { queryKey: ['readiness'], queryFn: api.readiness, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: canSeeOps },
      { queryKey: ['signoff'], queryFn: api.signoff, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: canSeeOps },
    ],
  })
  const bulletins = useQuery({ queryKey: ['bulletins'], queryFn: api.bulletins, refetchInterval: autoRefresh.enabled ? 30000 : false })
  const accounts = useQuery({ queryKey: ['channelAccounts'], queryFn: api.channelAccounts, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: canSeeChannels })

  const q = queue.data
  const rt = runtime.data
  const rd = readiness.data
  const so = signoff.data
  const todayMetrics = today.data?.metrics ?? {}
  const todayTasks = today.data?.tasks ?? []
  const slaRiskTickets = today.data?.sla_risk_tickets ?? []
  const runtimeRecoveryCount = (q?.dead_jobs ?? 0) + (q?.dead_outbound ?? 0) + (rt?.dead_sync_jobs ?? 0) + (rt?.dead_attachment_jobs ?? 0)
  const needsRuntimeRecovery = canSeeOps && runtimeRecoveryCount > 0

  const openTask = (task: TodayWorkbenchTask) => {
    if (task.target_route === '/webchat') navigate({ to: '/webchat' })
    else if (task.target_route === '/email') navigate({ to: '/email' })
    else if (task.target_route === '/runtime') navigate({ to: '/runtime' })
    else navigate({ to: '/workspace' })
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="BUSINESS OPERATIONS HOME"
        title="今日工作台 / 我的优先事项"
        description="按当前账号权限聚合真实队列、SLA 风险、客户等待和下一步动作；运营保障仍只对有权限的主管或管理员显示。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={async () => { await client.invalidateQueries(); setToast({ message: '今日工作台数据已刷新', tone: 'success' }) }}>立即刷新</Button></div>}
      />

      {today.isError ? (
        <div className="message" data-role="agent">今日工作台需要 ticket.read 权限；请联系主管检查当前账号授权。</div>
      ) : null}

      <div className="metrics-grid metrics-grid-wide" data-testid="today-workbench-metrics">
        <MetricCard label="我的处理中工单" value={today.data ? todayMetrics.my_open_tickets ?? 0 : '—'} hint="按当前账号可见范围计算" />
        <MetricCard label="30 分钟 SLA 风险" value={today.data ? todayMetrics.sla_risk_30m ?? 0 : '—'} hint="含即将超时和已超时" />
        <MetricCard label="待人工接入" value={today.data ? todayMetrics.webchat_handoff_requested ?? 0 : '—'} hint="WebChat handoff 请求" />
        <MetricCard label="WebChat 待回复" value={today.data ? todayMetrics.webchat_waiting ?? 0 : '—'} hint="需要人工关注的实时会话" />
        <MetricCard label="等待中的 Email" value={today.data ? todayMetrics.email_waiting ?? 0 : '—'} hint="邮件来源工单队列" />
        <MetricCard label="客户等待处理" value={today.data ? todayMetrics.customer_waiting ?? 0 : '—'} hint="客户已回复待跟进" />
      </div>

      <Card className="soft">
        <CardHeader title="角色任务" subtitle="任务数量受当前账号权限和可见工单范围约束。" />
        <CardBody>
          <div className="guide-grid" data-testid="today-workbench-tasks">
            {todayTasks.map((task) => (
              <div className="guide-item" key={task.key}>
                <div className="badges">
                  <Badge tone={taskTone(task.severity)}>{sanitizeDisplayText(task.count)}</Badge>
                  <Badge>{sanitizeDisplayText(task.title)}</Badge>
                </div>
                <strong>{sanitizeDisplayText(task.next)}</strong>
                <span>{task.key === 'sla-risk' ? '优先查看临近 SLA 的客户请求。' : '打开对应工作台继续处理。'}</span>
                <Button variant={task.severity === 'danger' && task.count > 0 ? 'primary' : 'secondary'} onClick={() => openTask(task)}>打开</Button>
              </div>
            ))}
            {!todayTasks.length ? <div className="empty">正在加载今日任务。</div> : null}
          </div>
        </CardBody>
      </Card>

      <div className="page-grid split-grid">
        <Card>
          <CardHeader title="临近 SLA 工单" subtitle="按当前账号可见范围和 30 分钟窗口排序。" />
          <CardBody>
            <div className="list" data-testid="today-workbench-sla-risk">
              {slaRiskTickets.map((item) => (
                <div className="list-item" key={item.id}>
                  <div className="badges">
                    <Badge tone={item.overdue ? 'danger' : 'warning'}>{item.overdue ? '已超时' : '临近 SLA'}</Badge>
                    <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>
                    <Badge tone={priorityTone(item.priority)}>{labelize(item.priority)}</Badge>
                    <Badge>{labelize(item.source_channel)}</Badge>
                  </div>
                  <div><strong>{sanitizeDisplayText(item.ticket_no)} · {sanitizeDisplayText(item.title)}</strong></div>
                  <div className="section-subtitle">下一截止：{formatDateTime(item.next_due_at)} · {sanitizeDisplayText(item.customer_name)} · {sanitizeDisplayText(item.assignee_name || item.team_name)}</div>
                  {item.required_action ? <div className="message" data-role="agent">{sanitizeDisplayText(item.required_action)}</div> : null}
                </div>
              ))}
              {!slaRiskTickets.length ? <div className="empty">当前没有 30 分钟内 SLA 风险。</div> : null}
            </div>
          </CardBody>
        </Card>
        <Card className="soft">
          <CardHeader title="优先处理入口" subtitle="把今天最常用的处理动作直接放到首页。" />
          <CardBody>
            <div className="guide-grid" data-testid="overview-priority-actions">
              <div className="guide-item">
                <strong>处理临近 SLA 工单</strong>
                <span>进入 Workspace，完成分配、回复、证据核对和处理结果保存。</span>
                <Button variant="primary" onClick={() => navigate({ to: '/workspace' })}>打开工单处理</Button>
              </div>
              <div className="guide-item">
                <strong>接入等待最久的 WebChat</strong>
                <span>处理网站实时会话、handoff、客户 action 和 WebChat 本地回复。</span>
                <Button variant="secondary" onClick={() => navigate({ to: '/webchat' })}>打开 WebChat 收件箱</Button>
              </div>
              {canSeeOps ? (
                <div className="guide-item">
                  <strong>{needsRuntimeRecovery ? `运行恢复待处理 ${runtimeRecoveryCount}` : '运行恢复'}</strong>
                  <span>{needsRuntimeRecovery ? '当前存在 dead/requeue 类异常，建议主管优先处理。' : '检查同步、队列、会话连接与恢复动作。'}</span>
                  <Button variant={needsRuntimeRecovery ? 'primary' : 'secondary'} onClick={() => navigate({ to: '/runtime' })}>打开运行恢复</Button>
                </div>
              ) : (
                <div className="guide-item">
                  <strong>处理等待中的 Email</strong>
                  <span>打开 Email 工作台，保存草稿、发送回复并确认 timeline 回写。</span>
                  <Button variant="secondary" onClick={() => navigate({ to: '/email' })}>打开 Email 工作台</Button>
                </div>
              )}
            </div>
          </CardBody>
        </Card>
      </div>

      {canSeeOps ? (
        <div className="metrics-grid metrics-grid-wide">
          <MetricCard label="待处理任务" value={q?.pending_jobs ?? '—'} hint="后台待执行任务" />
          <MetricCard label="异常任务" value={q?.dead_jobs ?? '—'} hint="需要人工排查" />
          <MetricCard label="已关联客户会话" value={q?.openclaw_links ?? '—'} hint="工单和客户来信已对上" />
          <MetricCard label="待补同步" value={rt?.stale_link_count ?? '—'} hint="需要补抓的客户消息" />
          <MetricCard label="待处理附件" value={rt?.pending_attachment_jobs ?? '—'} hint="证据或附件待落库" />
          <MetricCard label="提醒项" value={((rd?.warnings?.length ?? 0) + (rt?.warnings?.length ?? 0) + (so?.warnings?.length ?? 0)) || '0'} hint="建议先处理提醒项" />
        </div>
      ) : null}

      {canSeeOps ? (
        <div className="page-grid split-grid">
          <Card>
            <CardHeader title="运营准备情况" subtitle="上线前的配置状态与准备情况。" />
            <CardBody>
              <div className="button-row" style={{ marginBottom: 12 }}>
                <Button variant="secondary" onClick={async () => { const res = await api.consumeOpenClawEventsOnce(); setToast({ message: `已执行一次消息同步，处理 ${res.processed} 批`, tone: 'success' }); await client.invalidateQueries({ queryKey: ['runtimeHealth'] }); }}>执行一次消息同步</Button>
                {needsRuntimeRecovery ? <Button variant="primary" onClick={() => navigate({ to: '/runtime' })}>处理运行异常</Button> : null}
              </div>
              <div className="kv-grid">
                <div className="kv"><label>环境</label><div>{sanitizeDisplayText(rd?.app_env)}</div></div>
                <div className="kv"><label>数据库</label><div>{sanitizeDisplayText(rd?.database_url_scheme)}</div></div>
                <div className="kv"><label>附件存储</label><div>{sanitizeDisplayText(rd?.storage_backend)}</div></div>
                <div className="kv"><label>消息方式</label><div>{sanitizeDisplayText(rd?.openclaw_transport)}</div></div>
              </div>
              <div className="stack" style={{ marginTop: 12 }}>
                {(rd?.warnings ?? []).map((warning) => <div key={warning} className="message" data-role="agent">{sanitizeDisplayText(warning)}</div>)}
                {!(rd?.warnings?.length) ? <div className="empty">当前没有上线阻塞项。</div> : null}
              </div>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="消息同步状态" subtitle="会话同步、补拉和附件处理的健康度。" />
            <CardBody>
              <div className="kv-grid">
                <div className="kv"><label>同步游标</label><div>{sanitizeDisplayText(rt?.sync_cursor)}</div></div>
                <div className="kv"><label>当前状态</label><div>{sanitizeDisplayText(rt?.sync_daemon_status)}</div></div>
                <div className="kv"><label>最近心跳</label><div>{formatDateTime(rt?.sync_daemon_last_seen_at)}</div></div>
                <div className="kv"><label>失败同步任务</label><div>{rt?.dead_sync_jobs ?? '—'}</div></div>
              </div>
              <div className="stack" style={{ marginTop: 12 }}>
                {(rt?.warnings ?? []).map((warning) => <div key={warning} className="message" data-role="agent">{sanitizeDisplayText(warning)}</div>)}
                {!(rt?.warnings?.length) ? <div className="empty">消息同步状态正常。</div> : null}
              </div>
            </CardBody>
          </Card>
        </div>
      ) : (
        <Card>
          <CardHeader title="主管协同提醒" subtitle="一线客服默认不需要进入发送线路和运营保障页面。" />
          <CardBody>
            <div className="message" data-role="agent">如遇到消息发送异常、来信不同步、账号不可用等问题，请在工单里备注后交给主管或管理员处理。</div>
          </CardBody>
        </Card>
      )}

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

      {canSeeOps ? (
        <Card>
          <CardHeader title="上线检查清单" subtitle="高层查看是否可上线，一线查看哪里还没到位。" />
          <CardBody>
            <div className="kv-grid kv-grid-three">
              {Object.entries(so?.checks ?? {}).map(([key, value]) => (
                <div key={key} className="kv">
                  <label>{signoffLabel(key)}</label>
                  <div>{value ? <Badge tone="success">通过</Badge> : <Badge tone="danger">未通过</Badge>}</div>
                </div>
              ))}
            </div>
            {(so?.warnings?.length ?? 0) > 0 ? (
              <div className="stack" style={{ marginTop: 12 }}>
                {so!.warnings.map((warning) => <div key={warning} className="message" data-role="agent">{sanitizeDisplayText(warning)}</div>)}
              </div>
            ) : null}
          </CardBody>
        </Card>
      ) : null}
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
