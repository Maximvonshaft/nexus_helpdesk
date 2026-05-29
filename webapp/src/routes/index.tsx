import { useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, labelize, sanitizeDisplayText, severityTone, signoffLabel } from '@/lib/format'
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

function badgeTone(tone?: string | null): 'default' | 'warning' | 'success' | 'danger' {
  if (tone === 'warning' || tone === 'success' || tone === 'danger') return tone
  return 'default'
}

function waitLabel(seconds?: number | null) {
  if (!seconds) return null
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function OverviewPage() {
  const client = useQueryClient()
  const navigate = useNavigate()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const canSeeOps = canViewOps(session.data)
  const canSeeChannels = canManageChannels(session.data)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [queue, runtime, readiness, signoff] = useQueries({
    queries: [
      { queryKey: ['queueSummary'], queryFn: api.queueSummary, refetchInterval: autoRefresh.enabled ? 15000 : false, enabled: canSeeOps },
      { queryKey: ['runtimeHealth'], queryFn: api.runtimeHealth, refetchInterval: autoRefresh.enabled ? 15000 : false, enabled: canSeeOps },
      { queryKey: ['readiness'], queryFn: api.readiness, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: canSeeOps },
      { queryKey: ['signoff'], queryFn: api.signoff, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: canSeeOps },
    ],
  })
  const workbench = useQuery({ queryKey: ['workbenchSummary'], queryFn: () => api.workbenchSummary({ limit: 12 }), refetchInterval: autoRefresh.enabled ? 15000 : false })
  const bulletins = useQuery({ queryKey: ['bulletins'], queryFn: api.bulletins, refetchInterval: autoRefresh.enabled ? 30000 : false })
  const accounts = useQuery({ queryKey: ['channelAccounts'], queryFn: api.channelAccounts, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: canSeeChannels })

  const q = queue.data
  const rt = runtime.data
  const rd = readiness.data
  const so = signoff.data
  const wb = workbench.data
  const runtimeRecoveryCount = (q?.dead_jobs ?? 0) + (q?.dead_outbound ?? 0) + (rt?.dead_sync_jobs ?? 0) + (rt?.dead_attachment_jobs ?? 0)
  const needsRuntimeRecovery = canSeeOps && runtimeRecoveryCount > 0

  function goRoute(route?: string | null) {
    if (route === '/webchat') navigate({ to: '/webchat' })
    else if (route === '/webcall') navigate({ to: '/webcall' })
    else if (route === '/email') navigate({ to: '/email' })
    else if (route === '/runtime') navigate({ to: '/runtime' })
    else navigate({ to: '/workspace' })
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="角色工作台"
        title="今日工作台 / 我的优先事项"
        description="来自真实工单、WebChat handoff、WebCall 会话、Email/outbound 和 SLA 字段的当班优先队列。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={async () => { await client.invalidateQueries(); setToast({ message: '今日工作台数据已刷新', tone: 'success' }) }}>立即刷新</Button></div>}
      />

      <div className="metrics-grid metrics-grid-wide" data-testid="today-workbench-metrics">
        {(wb?.metrics ?? []).map((metric) => (
          <button key={metric.key} className="card metric metric-button" onClick={() => goRoute(metric.target_route)}>
            <div className="metric-value">{metric.value}</div>
            <div className="metric-label">{sanitizeDisplayText(metric.label)}</div>
            {metric.hint ? <div className="section-subtitle">{sanitizeDisplayText(metric.hint)}</div> : null}
          </button>
        ))}
        {workbench.isLoading ? <MetricCard label="正在加载" value="..." hint="读取今日工作台 API" /> : null}
        {workbench.isError ? <MetricCard label="工作台 API" value="!" hint="无法读取 /api/workbench/summary" /> : null}
      </div>

      <Card className="soft" data-testid="today-workbench-role-tasks">
        <CardHeader title="今日优先事项" subtitle="按角色权限和真实后端状态生成，直接跳转到可处理入口。" />
        <CardBody>
          <div className="guide-grid">
            {(wb?.tasks ?? []).map((task) => (
              <div className="guide-item" key={task.id}>
                <div className="badges">
                  <Badge tone={badgeTone(task.severity)}>{task.count}</Badge>
                  <Badge>{sanitizeDisplayText(task.source)}</Badge>
                </div>
                <strong>{sanitizeDisplayText(task.title)}</strong>
                <span>{sanitizeDisplayText(task.next_action)}</span>
                <Button variant={task.count > 0 ? 'primary' : 'secondary'} onClick={() => goRoute(task.target_route)}>打开处理入口</Button>
              </div>
            ))}
            {!workbench.isLoading && !(wb?.tasks ?? []).length ? <div className="empty">暂无需要优先处理的角色任务。</div> : null}
          </div>
        </CardBody>
      </Card>

      <Card className="soft">
        <CardHeader title="优先处理入口" subtitle="把高频动作直接放到首页，避免客服主管在多个页面之间来回找入口。" />
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
                <span>{needsRuntimeRecovery ? '当前存在 dead/requeue 类异常，建议主管优先处理。' : '检查同步、队列、OpenClaw 连接与恢复动作。'}</span>
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
        <Card data-testid="today-workbench-sla-queue">
          <CardHeader title="SLA 与优先队列" subtitle="WebCall、WebChat handoff、Email 与普通工单统一排序。" />
          <CardBody>
            <div className="list">
              {(wb?.queue ?? []).map((item) => (
                <button key={item.id} className="list-item align-left" onClick={() => goRoute(item.target_route)}>
                  <div className="badges">
                    <Badge tone={item.overdue ? 'danger' : badgeTone(item.kind === 'webcall' || item.kind === 'webchat_handoff' ? 'warning' : 'default')}>{labelize(item.kind)}</Badge>
                    <Badge>{labelize(item.status)}</Badge>
                    {item.priority ? <Badge>{labelize(item.priority)}</Badge> : null}
                    {item.waiting_seconds ? <Badge tone="warning">等待 {waitLabel(item.waiting_seconds)}</Badge> : null}
                  </div>
                  <div><strong>{sanitizeDisplayText(item.ticket_no || `#${item.ticket_id}`)} · {sanitizeDisplayText(item.title)}</strong></div>
                  <div className="section-subtitle">{sanitizeDisplayText(item.recommended_action)} · {formatDateTime(item.due_at || item.updated_at || undefined)}</div>
                </button>
              ))}
              {!workbench.isLoading && !(wb?.queue ?? []).length ? <div className="empty">当前没有待处理队列项。</div> : null}
            </div>
          </CardBody>
        </Card>
        <Card data-testid="today-workbench-interaction-states">
          <CardHeader title="交互状态表" subtitle="模板中的 interaction-state table，按真实后端状态聚合。" />
          <CardBody>
            <DataTable
              loading={workbench.isLoading}
              columns={['状态', '数量', '处理入口']}
              rows={(wb?.interaction_states ?? []).map((state) => [
                <span className="badges" key={`${state.key}-state`}><Badge tone={badgeTone(state.tone)}>{sanitizeDisplayText(state.label)}</Badge></span>,
                state.count,
                <Button key={`${state.key}-action`} variant={state.count > 0 ? 'primary' : 'secondary'} onClick={() => goRoute(state.target_route)}>打开</Button>,
              ])}
            />
            <div className="section-subtitle" style={{ marginTop: 12 }}>数据源：{(wb?.data_sources ?? ['/api/workbench/summary']).map(sanitizeDisplayText).join(' / ')}</div>
          </CardBody>
        </Card>
      </div>

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
