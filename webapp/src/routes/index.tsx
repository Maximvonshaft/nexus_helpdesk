import { useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
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

function OverviewPage() {
  const client = useQueryClient()
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
  const bulletins = useQuery({ queryKey: ['bulletins'], queryFn: api.bulletins, refetchInterval: autoRefresh.enabled ? 30000 : false })
  const caseFeed = useQuery({ queryKey: ['overviewCases'], queryFn: () => api.cases(), refetchInterval: autoRefresh.enabled ? 30000 : false })
  const accounts = useQuery({ queryKey: ['channelAccounts'], queryFn: api.channelAccounts, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: canSeeChannels })

  const q = queue.data
  const rt = runtime.data
  const rd = readiness.data
  const so = signoff.data

  return (
    <AppShell>
      <PageHeader
        eyebrow="首页总览"
        title="今天的客服全局情况"
        description="先看待处理工单和提醒项，再看公告口径；主管再补看运营保障，一线客服也能快速抓重点。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={async () => { await client.invalidateQueries(); setToast({ message: '首页数据已刷新', tone: 'success' }) }}>立即刷新</Button></div>}
      />

      <Card className="soft">
        <CardHeader title="当班建议" subtitle="让客服同事先按顺序处理，而不是在多个模块里来回找重点。" />
        <CardBody>
          <div className="guide-grid">
            <div className="guide-item"><strong>1. 先看待处理任务</strong><span>优先处理最新客户来信、紧急单和等待中的客户回复。</span></div>
            <div className="guide-item"><strong>2. 再看当前公告</strong><span>确认今天是否有延误、清关、异常公告影响回复口径。</span></div>
            <div className="guide-item"><strong>3. 最后补看运营保障</strong><span>{canSeeOps ? '你当前有权限查看发送线路与系统健康。' : '运营保障页面由主管或管理员处理，普通客服优先专注工单。'}</span></div>
          </div>
        </CardBody>
      </Card>

      <div className="metrics-grid metrics-grid-wide">
        {canSeeOps ? (
          <>
            <MetricCard label="待处理任务" value={q?.pending_jobs ?? '—'} hint="后台待执行任务" />
            <MetricCard label="异常任务" value={q?.dead_jobs ?? '—'} hint="需要人工排查" />
            <MetricCard label="已关联客户会话" value={q?.openclaw_links ?? '—'} hint="工单和客户来信已对上" />
            <MetricCard label="待补同步" value={rt?.stale_link_count ?? '—'} hint="需要补抓的客户消息" />
            <MetricCard label="待处理附件" value={rt?.pending_attachment_jobs ?? '—'} hint="证据或附件待落库" />
            <MetricCard label="提醒项" value={((rd?.warnings?.length ?? 0) + (rt?.warnings?.length ?? 0) + (so?.warnings?.length ?? 0)) || '0'} hint="建议先处理提醒项" />
          </>
        ) : (
          <>
            <MetricCard label="我的工单总数" value={caseFeed.data?.length ?? '—'} hint="当前账号能看到的工单" />
            <MetricCard label="处理中" value={(caseFeed.data ?? []).filter((item) => item.status === 'in_progress').length} hint="建议优先处理最新客户来信" />
            <MetricCard label="待客户回复" value={(caseFeed.data ?? []).filter((item) => item.status === 'waiting_customer').length} hint="需要继续跟进客户反馈" />
            <MetricCard label="已解决" value={(caseFeed.data ?? []).filter((item) => item.status === 'resolved').length} hint="可用于交接与复盘" />
            <MetricCard label="生效公告" value={(bulletins.data ?? []).filter((item) => item.is_active).length} hint="会影响当前回复口径" />
            <MetricCard label="今日提醒" value={(bulletins.data ?? []).filter((item) => item.is_active && item.severity === 'critical').length} hint="优先关注紧急公告" />
          </>
        )}
      </div>

      {canSeeOps ? (
        <div className="page-grid split-grid">
          <Card>
            <CardHeader title="运营准备情况" subtitle="上线前的配置状态与准备情况。" />
            <CardBody>
              <div className="button-row" style={{ marginBottom: 12 }}>
                <Button variant="secondary" onClick={async () => { const res = await api.consumeOpenClawEventsOnce(); setToast({ message: `已执行一次消息同步，处理 ${res.processed} 批`, tone: 'success' }); await client.invalidateQueries({ queryKey: ['runtimeHealth'] }); }}>执行一次消息同步</Button>
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
