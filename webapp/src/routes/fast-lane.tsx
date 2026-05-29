import { useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { Select } from '@/components/ui/Field'
import { Skeleton } from '@/components/ui/Skeleton'
import { RequireCapability } from '@/components/security/RequireCapability'
import { formatDateTime, labelize } from '@/lib/format'
import { routeAccess } from '@/lib/rbac'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'

const FAST_LANE_RUNTIME_CONTRACTS = [
  '/api/webchat/fast-reply',
  '/api/webchat/fast-reply/stream',
  'idempotency begin / replay / conflict',
  'server handoff policy before provider',
  'tracking fact-first response',
]

function formatNumber(value?: number | null) {
  return new Intl.NumberFormat('en-US').format(Number(value ?? 0))
}

function formatPercent(rate?: number | null) {
  return `${Math.round(Number(rate ?? 0) * 100)}%`
}

function mapRows(map?: Record<string, number>) {
  return Object.entries(map ?? {})
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([key, value]) => [labelize(key), formatNumber(value)])
}

function ratioRows(stats: {
  total_sessions: number
  ticketless_sessions: number
  ai_resolved_sessions: number
  handoff_sessions: number
}) {
  const total = Math.max(stats.total_sessions, 1)
  return [
    ['Ticketless sessions', stats.ticketless_sessions],
    ['AI resolved sessions', stats.ai_resolved_sessions],
    ['Handoff sessions', stats.handoff_sessions],
  ].map(([label, rawValue]) => {
    const value = Number(rawValue)
    return [
      String(label),
      formatNumber(value),
      `${Math.round((value / total) * 100)}%`,
      <div className="progress-track" aria-label={`${label} ratio`}><span style={{ width: `${Math.min(100, Math.round((value / total) * 100))}%` }} /></div>,
    ]
  })
}

function FastLanePage() {
  const [days, setDays] = useState(7)
  const autoRefresh = useAutoRefresh(true)
  const client = useQueryClient()
  const stats = useQuery({
    queryKey: ['webchatFastStats', days],
    queryFn: () => api.webchatFastStats(days),
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })

  const errorRows = useMemo(() => mapRows(stats.data?.errors_by_code), [stats.data?.errors_by_code])
  const intentRows = useMemo(() => mapRows(stats.data?.sessions_by_intent), [stats.data?.sessions_by_intent])
  const idempotencyRows = useMemo(() => mapRows(stats.data?.idempotency_by_status), [stats.data?.idempotency_by_status])
  const distributionRows = useMemo(() => stats.data ? ratioRows(stats.data) : [], [stats.data])
  const totalSessions = stats.data?.total_sessions ?? 0
  const aiResolvedRate = totalSessions ? (stats.data?.ai_resolved_sessions ?? 0) / totalSessions : 0
  const handoffRate = stats.data?.handoff_rate ?? 0

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/fast-lane']}>
        <PageHeader
          eyebrow="AI DEFLECTION"
          title="Fast Lane / AI Deflection 看板"
          description="用真实 Fast Lane 统计同时看 ticketless sessions、AI resolved、handoff rate、error codes 和 intent。"
          actions={
            <div className="button-row">
              <Select value={String(days)} onChange={(event) => setDays(Number(event.target.value))} aria-label="Fast Lane window">
                <option value="7">近 7 天</option>
                <option value="30">近 30 天</option>
                <option value="90">近 90 天</option>
              </Select>
              <Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>
                {autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}
              </Button>
              <Button onClick={() => client.invalidateQueries({ queryKey: ['webchatFastStats'] })} disabled={stats.isFetching}>
                {stats.isFetching ? '刷新中...' : '立即刷新'}
              </Button>
            </div>
          }
        />

        <div data-testid="fast-lane-workbench" className="stack">
          {stats.isLoading ? <Skeleton lines={6} /> : null}
          {stats.isError ? <ErrorSummary title="Fast Lane 统计加载失败" errors={[(stats.error as Error).message || '无法读取 /api/stats/webchat-fast']} /> : null}

          {stats.data ? (
            <>
              <div className="metrics-grid">
                <MetricCard label="Total sessions" value={formatNumber(stats.data.total_sessions)} hint={`since ${formatDateTime(stats.data.since)}`} />
                <MetricCard label="Ticketless sessions" value={formatNumber(stats.data.ticketless_sessions)} hint="AI resolved without ticket" />
                <MetricCard label="AI resolved rate" value={formatPercent(aiResolvedRate)} hint={`${formatNumber(stats.data.ai_resolved_sessions)} AI sessions`} />
                <MetricCard label="Handoff rate" value={formatPercent(handoffRate)} hint={`${formatNumber(stats.data.handoff_sessions)} human handoffs`} />
              </div>

              <div className="page-grid split-grid-wide">
                <Card className="soft">
                  <CardHeader title="Operational Interpretation" subtitle="Ticket volume alone is no longer enough." />
                  <CardBody>
                    <DataTable columns={['Metric', 'Sessions', 'Share', 'Trend']} rows={distributionRows} />
                    <div className="badges" style={{ marginTop: 12 }}>
                      <Badge tone={aiResolvedRate >= 0.6 ? 'success' : 'warning'}>AI resolved {formatPercent(aiResolvedRate)}</Badge>
                      <Badge tone={handoffRate >= 0.35 ? 'warning' : 'success'}>handoff {formatPercent(handoffRate)}</Badge>
                      <Badge>customer messages {formatNumber(stats.data.customer_messages)}</Badge>
                      <Badge>AI messages {formatNumber(stats.data.ai_messages)}</Badge>
                    </div>
                  </CardBody>
                </Card>

                <Card>
                  <CardHeader title="Fast Reply Runtime Contract" subtitle="当前看板读取真实统计端点；运行契约仍由 Fast Lane API 和幂等链路承载。" />
                  <CardBody>
                    <DataTable columns={['Contract', 'Status']} rows={FAST_LANE_RUNTIME_CONTRACTS.map((item) => [item, 'tracked'])} />
                  </CardBody>
                </Card>
              </div>

              <div className="page-grid split-grid">
                <Card>
                  <CardHeader title="Error Codes" subtitle="失败原因来自 Fast Lane idempotency error_code 聚合。" />
                  <CardBody>
                    {errorRows.length ? <DataTable columns={['Code', 'Count']} rows={errorRows} /> : <EmptyState title="没有错误码" description="当前时间窗口内没有 Fast Lane error_code。" />}
                  </CardBody>
                </Card>

                <Card>
                  <CardHeader title="Intent Distribution" subtitle="按会话 last_intent 聚合，用于判断客户需求结构。" />
                  <CardBody>
                    {intentRows.length ? <DataTable columns={['Intent', 'Sessions']} rows={intentRows} /> : <EmptyState title="没有 intent 数据" description="当前时间窗口内没有 Fast Lane 会话 intent。" />}
                  </CardBody>
                </Card>
              </div>

              <Card>
                <CardHeader title="Idempotency Status" subtitle="确认 begin/replay/conflict/done/failed 等状态没有隐藏运行风险。" />
                <CardBody>
                  {idempotencyRows.length ? <DataTable columns={['Status', 'Requests']} rows={idempotencyRows} /> : <EmptyState title="没有幂等状态数据" description="当前时间窗口内没有 Fast Lane 幂等记录。" />}
                </CardBody>
              </Card>
            </>
          ) : null}
        </div>
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/fast-lane',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: FastLanePage,
})
