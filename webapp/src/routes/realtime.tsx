import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { routeAccess } from '@/lib/rbac'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { PageHeader } from '@/components/ui/PageHeader'
import { RequireCapability } from '@/components/security/RequireCapability'

function enabledLabel(value?: boolean) {
  return value ? '已启用' : '未启用'
}

function enabledTone(value?: boolean) {
  return value ? 'success' : 'warning'
}

function frontendWsEnabled() {
  return String(import.meta.env.VITE_WEBCHAT_WS_ENABLED ?? 'true').toLowerCase() !== 'false'
}

function RealtimeHealthPage() {
  const autoRefresh = useAutoRefresh(true)
  const client = useQueryClient()
  const health = useQuery({
    queryKey: ['webchatRealtimeHealth'],
    queryFn: api.webchatRealtimeHealth,
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })
  const data = health.data
  const frontendEnabled = frontendWsEnabled()
  const eventTypeRows = Object.entries(data?.events.event_types ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => [sanitizeDisplayText(type), String(count)])

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/realtime']}>
        <PageHeader
          eyebrow="WebChat Realtime"
          title="Realtime Health"
          description="把 WebSocket、fallback polling、replay cursor、broker 和连接限额从后端事实直接暴露给运营与审计。"
          actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停自动刷新' : '开启自动刷新'}</Button><Button onClick={() => client.invalidateQueries({ queryKey: ['webchatRealtimeHealth'] })} disabled={health.isFetching}>{health.isFetching ? '刷新中...' : '刷新'}</Button></div>}
        />

        {health.isError ? (
          <Card>
            <CardHeader title="实时链路状态读取失败" subtitle={(health.error as Error).message} />
          </Card>
        ) : null}

        <div className="metrics-grid" data-testid="realtime-health-workbench">
          <Card className="metric">
            <div className="metric-label">WebSocket 后端</div>
            <div className="metric-value"><Badge tone={enabledTone(data?.enabled)}>{enabledLabel(data?.enabled)}</Badge></div>
          </Card>
          <Card className="metric">
            <div className="metric-label">前端开关</div>
            <div className="metric-value"><Badge tone={enabledTone(frontendEnabled)}>{enabledLabel(frontendEnabled)}</Badge></div>
          </Card>
          <Card className="metric">
            <div className="metric-label">当前连接</div>
            <div className="metric-value">{data?.hub.connections ?? '—'}</div>
          </Card>
          <Card className="metric">
            <div className="metric-label">Replay Cursor</div>
            <div className="metric-value">{data?.events.last_event_id ?? '—'}</div>
          </Card>
        </div>

        <div className="page-grid split-grid">
          <Card>
            <CardHeader title="运行时配置" subtitle="这些值来自真实后端 settings 和 broker，不是前端常量。" />
            <CardBody>
              <DataTable columns={['项目', '值']} rows={[
                ['WS path', sanitizeDisplayText(data?.ws_path)],
                ['Agent WS', <Badge tone={enabledTone(data?.admin_enabled)}>{enabledLabel(data?.admin_enabled)}</Badge>],
                ['Public WS', <Badge tone={enabledTone(data?.public_enabled)}>{enabledLabel(data?.public_enabled)}</Badge>],
                ['Broker', sanitizeDisplayText(data?.broker.name)],
                ['Durable replay', data?.broker.durable_replay ? '是' : '否'],
                ['Cross-worker safe', data?.broker.cross_worker_safe ? '是' : '否'],
                ['Replay poll', `${data?.replay_poll_ms ?? '—'} ms`],
                ['Fallback poll', `${data?.fallback_poll_ms ?? '—'} ms`],
                ['Heartbeat', `${data?.heartbeat_ms ?? '—'} ms`],
                ['Hello timeout', `${data?.hello_timeout_ms ?? '—'} ms`],
              ]} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="连接与订阅" subtitle="In-process hub 只做唤醒与活跃连接视图，Durable replay 仍以数据库事件为准。" />
            <CardBody>
              <DataTable columns={['项目', '值']} rows={[
                ['Agent connections', String(data?.hub.agents ?? '—')],
                ['Visitor connections', String(data?.hub.visitors ?? '—')],
                ['Subscriptions', String(data?.hub.subscriptions ?? '—')],
                ['Max connections', String(data?.max_connections ?? '—')],
                ['Max per user/conversation', String(data?.max_connections_per_user ?? '—')],
                ['Recent events scanned', String(data?.events.recent_event_count ?? '—')],
                ['Last event at', formatDateTime(data?.events.last_event_at)],
              ]} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="最近事件类型" subtitle="按最近 100 条 WebChat durable events 聚合，用于判断 replay 是否仍有写入。" />
            <CardBody>
              <DataTable columns={['事件类型', '数量']} rows={eventTypeRows.length ? eventTypeRows : [['暂无事件', '0']]} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="告警与降级" subtitle="WebSocket 不可用时，WebChat 工作台必须继续通过 after_id polling fallback 工作。" />
            <CardBody>
              <div className="detail-grid">
                <div><span>Fallback contract</span><strong>after_id polling</strong></div>
                <div><span>Replay source</span><strong>webchat_events</strong></div>
                <div><span>Auth transport</span><strong>connection.hello</strong></div>
                <div><span>Token in URL</span><strong>禁止</strong></div>
              </div>
              {(data?.warnings ?? []).map((warning) => <div className="message warning" key={warning}>{sanitizeDisplayText(warning)}</div>)}
              {data && data.warnings.length === 0 ? <div className="message success">实时链路未报告降级告警。</div> : null}
            </CardBody>
          </Card>
        </div>
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/realtime',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: RealtimeHealthPage,
})
