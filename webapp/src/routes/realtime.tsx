import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Skeleton } from '@/components/ui/Skeleton'
import { RequireCapability } from '@/components/security/RequireCapability'
import { routeAccess } from '@/lib/rbac'

function valueOrDash(value?: string | number | boolean | null) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? 'enabled' : 'disabled'
  return sanitizeDisplayText(String(value))
}

function statusTone(status?: string | null): 'default' | 'warning' | 'success' | 'danger' {
  if (status === 'ready') return 'success'
  if (status === 'disabled') return 'danger'
  if (status === 'degraded') return 'warning'
  return 'default'
}

function boolTone(value?: boolean | null): 'default' | 'warning' | 'success' | 'danger' {
  return value ? 'success' : 'warning'
}

function RealtimePage() {
  const queryClient = useQueryClient()
  const realtime = useQuery({
    queryKey: ['realtimeHealth'],
    queryFn: api.realtimeHealth,
    refetchInterval: 10000,
    retry: false,
  })

  const data = realtime.data

  async function refreshRealtime() {
    await queryClient.invalidateQueries({ queryKey: ['realtimeHealth'] })
  }

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/realtime']}>
        <PageHeader
          eyebrow="REALTIME RUNTIME"
          title="Realtime Health / WebSocket Fallback"
          description="WebChat WebSocket、broker、heartbeat、fallback polling、replay 和鉴权失败观测集中在这里。"
          actions={<Button variant="secondary" onClick={() => void refreshRealtime()} disabled={realtime.isFetching}>{realtime.isFetching ? '刷新中...' : '刷新'}</Button>}
        />

        {realtime.isLoading ? <Skeleton lines={6} /> : null}
        {realtime.isError ? (
          <Card>
            <CardHeader title="Realtime Health 加载失败" subtitle="该页面读取 /api/admin/realtime-health，需要 runtime.manage 权限。" />
            <CardBody>
              <EmptyState text={realtime.error instanceof Error ? realtime.error.message : '无法读取 realtime health。'} />
            </CardBody>
          </Card>
        ) : null}

        {data ? (
          <>
            <div className="metrics-grid" data-testid="realtime-health-metrics">
              <Card className="metric"><div className="metric-value">{data.features.enabled ? 'ON' : 'OFF'}</div><div className="metric-label">WS Enabled</div></Card>
              <Card className="metric"><div className="metric-value">{valueOrDash(data.features.broker)}</div><div className="metric-label">Broker</div></Card>
              <Card className="metric"><div className="metric-value">{data.features.heartbeat_ms}ms</div><div className="metric-label">Heartbeat</div></Card>
              <Card className="metric"><div className="metric-value">{data.features.fallback_poll_ms}ms</div><div className="metric-label">Fallback Poll</div></Card>
              <Card className="metric"><div className="metric-value">{valueOrDash(data.replay.last_event_id)}</div><div className="metric-label">Last Event ID</div></Card>
              <Card className="metric"><div className="metric-value">{valueOrDash(data.observability.auth_failures_total)}</div><div className="metric-label">Auth Failures</div></Card>
            </div>

            <div className="page-grid split-grid" data-testid="realtime-health-workbench">
              <Card>
                <CardHeader title="Runtime State" subtitle="来自真实 WebSocket settings、broker 状态和 in-process hub snapshot。" />
                <CardBody>
                  <div className="badges">
                    <Badge tone={statusTone(data.status)}>{labelize(data.status)}</Badge>
                    <Badge tone={boolTone(data.features.broker_durable_replay)}>durable replay</Badge>
                    <Badge tone={boolTone(data.features.broker_cross_worker_safe)}>cross-worker {data.features.broker_cross_worker_safe ? 'safe' : 'risk'}</Badge>
                  </div>
                  <DataTable columns={['项目', '值']} rows={[
                    ['Admin WS', valueOrDash(data.features.admin_enabled)],
                    ['Public WS', valueOrDash(data.features.public_enabled)],
                    ['Replay poll', `${data.features.replay_poll_ms}ms`],
                    ['Hello timeout', `${data.features.hello_timeout_ms}ms`],
                    ['Connection limit', String(data.features.max_connections)],
                    ['Per-user limit', String(data.features.max_connections_per_user)],
                  ]} />
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="Active Connections" subtitle="当前进程内连接、客服、访客和订阅数量。" />
                <CardBody>
                  <DataTable columns={['项目', '值']} rows={[
                    ['Connections', String(data.connections.connections)],
                    ['Agents', String(data.connections.agents)],
                    ['Visitors', String(data.connections.visitors)],
                    ['Subscriptions', String(data.connections.subscriptions)],
                  ]} />
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="Replay / Polling Evidence" subtitle="Durable WebchatEvent replay 与 fallback polling 的可观测事实。" />
                <CardBody>
                  <DataTable columns={['项目', '值']} rows={[
                    ['Last event ID', valueOrDash(data.replay.last_event_id)],
                    ['Last event at', formatDateTime(data.replay.last_event_at || undefined)],
                    ['Events last 5m', String(data.replay.events_last_5m)],
                    ['Handoff events last 5m', String(data.replay.handoff_events_last_5m)],
                    ['Conversation events last 5m', String(data.replay.conversation_events_last_5m)],
                  ]} />
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="WebSocket Observability" subtitle="Prometheus counter snapshot，和 /metrics 中的 WebChat WebSocket 指标同源。" />
                <CardBody>
                  <DataTable columns={['指标', '累计值']} rows={[
                    ['Connected', valueOrDash(data.observability.connected_total)],
                    ['Disconnected', valueOrDash(data.observability.disconnected_total)],
                    ['Auth failures', valueOrDash(data.observability.auth_failures_total)],
                    ['Events sent', valueOrDash(data.observability.event_sent_total)],
                    ['Replay batches', valueOrDash(data.observability.event_replay_total)],
                    ['Fallback polling', valueOrDash(data.observability.fallback_polling_total)],
                  ]} />
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="Warnings" subtitle="这些状态会影响 WebChat/WebCall 工作台实时体验。" />
                <CardBody>
                  {data.warnings.length ? (
                    <div className="stack compact">
                      {data.warnings.map((warning) => (
                        <div className="message" data-role="agent" key={warning}>{sanitizeDisplayText(warning)}</div>
                      ))}
                    </div>
                  ) : (
                    <EmptyState text="当前 Realtime Health 没有运行警告。" />
                  )}
                </CardBody>
              </Card>
            </div>
          </>
        ) : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/realtime',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: RealtimePage,
})
