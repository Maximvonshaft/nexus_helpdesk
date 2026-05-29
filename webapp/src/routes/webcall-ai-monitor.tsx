import { useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import {
  api,
  getToken,
  type WebCallAIAdminEvent,
  type WebCallAIAdminSession,
  type WebCallAIHealth,
} from '@/lib/api'
import { actionAccess, canAccess, routeAccess } from '@/lib/rbac'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { useSession } from '@/hooks/useAuth'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { DataTable } from '@/components/ui/DataTable'
import { PageHeader } from '@/components/ui/PageHeader'
import { RequireCapability } from '@/components/security/RequireCapability'
import { Toast } from '@/components/ui/Toast'

type SessionStatusFilter = 'active' | 'all' | 'failed' | 'ended'

function statusTone(value?: string | null): 'success' | 'warning' | 'danger' | 'default' {
  if (value === 'ready' || value === 'ready_for_internal_smoke' || value === 'joined' || value === 'listening' || value === 'speaking') return 'success'
  if (value === 'blocked' || value === 'failed' || value === 'kill_switch') return 'danger'
  if (value === 'degraded' || value === 'waiting_for_worker' || value === 'claimed' || value === 'thinking') return 'warning'
  return 'default'
}

function boolBadge(value?: boolean) {
  return <Badge tone={value ? 'success' : 'warning'}>{value ? 'yes' : 'no'}</Badge>
}

function eventSummary(event: WebCallAIAdminEvent) {
  const payload = event.payload || {}
  for (const key of ['text_redacted', 'reason', 'tool', 'tts_provider', 'status', 'error_code', 'voice_session_id']) {
    const value = payload[key]
    if (typeof value === 'string' && value.trim()) return sanitizeDisplayText(value)
  }
  return sanitizeDisplayText(JSON.stringify(payload).slice(0, 160) || 'Recorded')
}

function healthRows(health?: WebCallAIHealth) {
  return [
    ['Runtime status', <Badge tone={statusTone(health?.status)}>{sanitizeDisplayText(health?.status)}</Badge>],
    ['Smoke status', <Badge tone={statusTone(health?.smoke_status)}>{sanitizeDisplayText(health?.smoke_status)}</Badge>],
    ['Rollout mode', sanitizeDisplayText(health?.rollout_mode)],
    ['Provider profile', sanitizeDisplayText(health?.provider_profile)],
    ['STT / LLM / TTS', `${sanitizeDisplayText(health?.stt_provider)} / ${sanitizeDisplayText(health?.llm_provider)} / ${sanitizeDisplayText(health?.tts_provider)}`],
    ['LiveKit configured', boolBadge(health?.livekit_configured)],
    ['Tracking bridge', boolBadge(health?.tracking_bridge_configured)],
    ['Kill switch off', boolBadge(health ? !health.kill_switch : undefined)],
    ['Last heartbeat', formatDateTime(health?.last_heartbeat)],
  ]
}

function readinessRows(health?: WebCallAIHealth) {
  const readiness = health?.readiness
  return [
    ['LiveKit', boolBadge(readiness?.livekit_configured)],
    ['STT', boolBadge(readiness?.stt_configured)],
    ['LLM', boolBadge(readiness?.llm_configured)],
    ['TTS', boolBadge(readiness?.tts_configured)],
    ['Fake heartbeat disabled', boolBadge(readiness ? !readiness.fake_heartbeat_enabled : undefined)],
    ['Raw audio off', boolBadge(readiness ? !readiness.raw_audio_persistence : undefined)],
    ['Dangerous writes off', boolBadge(readiness ? !readiness.dangerous_write_actions_enabled : undefined)],
  ]
}

function sessionRows(
  sessions: WebCallAIAdminSession[],
  selectedId: string | null,
  onSelect: (id: string) => void,
  onForceEnd: (session: WebCallAIAdminSession) => void,
  canForceEnd: boolean,
  pending: boolean,
) {
  return sessions.map((session) => [
    <Button variant="ghost" onClick={() => onSelect(session.public_id)}>{sanitizeDisplayText(session.public_id)}</Button>,
    <Badge tone={statusTone(session.status)}>{sanitizeDisplayText(session.status)}</Badge>,
    <Badge tone={statusTone(session.ai_agent_status)}>{sanitizeDisplayText(session.ai_agent_status || 'unknown')}</Badge>,
    String(session.ai_turn_count ?? 0),
    formatDateTime(session.started_at),
    selectedId === session.public_id ? '已选中' : <Button variant="secondary" onClick={() => onSelect(session.public_id)}>查看事件</Button>,
    canForceEnd && !['ended', 'missed', 'failed', 'cancelled'].includes(session.status)
      ? <Button variant="secondary" disabled={pending} onClick={() => onForceEnd(session)}>Force end</Button>
      : <span className="section-subtitle">只读</span>,
  ])
}

function eventRows(events: WebCallAIAdminEvent[]) {
  return events.map((event) => [
    String(event.id),
    sanitizeDisplayText(event.event_type),
    eventSummary(event),
    formatDateTime(event.created_at),
  ])
}

function WebCallAIMonitorPage() {
  const session = useSession()
  const client = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<SessionStatusFilter>('active')
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const [pendingForceEnd, setPendingForceEnd] = useState<WebCallAIAdminSession | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const permitted = canAccess(session.data, routeAccess['/webcall-ai-monitor'])
  const canForceEnd = canAccess(session.data, actionAccess.forceEndWebcallAI)

  const health = useQuery({
    queryKey: ['webcallAIHealth'],
    queryFn: api.webcallAIHealth,
    refetchInterval: 15000,
    enabled: permitted,
  })
  const sessions = useQuery({
    queryKey: ['webcallAISessions', statusFilter],
    queryFn: () => api.webcallAISessions({ status: statusFilter, limit: 50 }),
    refetchInterval: 15000,
    enabled: permitted,
  })
  const activeSessionId = selectedSessionId || sessions.data?.items[0]?.public_id || null
  const events = useQuery({
    queryKey: ['webcallAISessionEvents', activeSessionId],
    queryFn: () => api.webcallAISessionEvents(activeSessionId as string),
    enabled: permitted && Boolean(activeSessionId),
    refetchInterval: 5000,
  })
  const forceEnd = useMutation({
    mutationFn: (sessionId: string) => api.webcallAIForceEndSession(sessionId),
    onSuccess: async () => {
      setToast({ message: 'WebCall AI session force-end 已写回后端。', tone: 'success' })
      await Promise.all([
        client.invalidateQueries({ queryKey: ['webcallAIHealth'] }),
        client.invalidateQueries({ queryKey: ['webcallAISessions'] }),
        client.invalidateQueries({ queryKey: ['webcallAISessionEvents'] }),
      ])
    },
    onError: (err: Error) => setToast({ message: err.message || 'Force-end 失败', tone: 'danger' }),
  })

  const blockerRows = useMemo(() => {
    const blockers = health.data?.readiness.blockers ?? []
    const degraded = health.data?.readiness.degraded ?? []
    return [
      ...blockers.map((item) => ['blocker', sanitizeDisplayText(item)]),
      ...degraded.map((item) => ['degraded', sanitizeDisplayText(item)]),
    ]
  }, [health.data])

  const sessionsData = sessions.data?.items ?? []
  const selectedSession = events.data?.session || sessionsData.find((item) => item.public_id === activeSessionId)

  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['webcallAIHealth'] }),
      client.invalidateQueries({ queryKey: ['webcallAISessions'] }),
      client.invalidateQueries({ queryKey: ['webcallAISessionEvents'] }),
    ])
  }

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/webcall-ai-monitor']}>
        <PageHeader
          eyebrow="WebCall AI"
          title="WebCall AI Monitor"
          description="生产 AI 语音入口的健康、会话、事件、租约和人工强制结束都从真实后端读取。"
          actions={<div className="button-row"><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as SessionStatusFilter)}><option value="active">Active</option><option value="all">All</option><option value="failed">Failed</option><option value="ended">Ended</option></select><Button onClick={() => void refresh()} disabled={health.isFetching || sessions.isFetching}>{health.isFetching || sessions.isFetching ? '刷新中...' : '刷新'}</Button></div>}
        />

        <div className="metrics-grid" data-testid="webcall-ai-monitor-workbench">
          <Card className="metric">
            <div className="metric-label">Runtime</div>
            <div className="metric-value"><Badge tone={statusTone(health.data?.status)}>{sanitizeDisplayText(health.data?.status)}</Badge></div>
          </Card>
          <Card className="metric">
            <div className="metric-label">Active AI Sessions</div>
            <div className="metric-value">{health.data?.active_sessions ?? '—'}</div>
          </Card>
          <Card className="metric">
            <div className="metric-label">Stale Leases</div>
            <div className="metric-value">{health.data?.stale_leases ?? '—'}</div>
          </Card>
          <Card className="metric">
            <div className="metric-label">Failed Sessions</div>
            <div className="metric-value">{health.data?.failed_sessions ?? '—'}</div>
          </Card>
        </div>

        {health.isError ? <div className="message warning">{(health.error as Error).message}</div> : null}

        <div className="page-grid split-grid">
          <Card>
            <CardHeader title="生产健康" subtitle="来自 /api/admin/webcall-ai/health 的真实 worker readiness 与配置状态。" />
            <CardBody><DataTable columns={['项目', '值']} rows={healthRows(health.data)} /></CardBody>
          </Card>
          <Card>
            <CardHeader title="Readiness Gate" subtitle="阻断项必须清零后才能进入内部 smoke；降级项需要上线前确认。" />
            <CardBody>
              <DataTable columns={['检查项', '状态']} rows={readinessRows(health.data)} />
              <DataTable columns={['类型', '项']} rows={blockerRows.length ? blockerRows : [['clear', 'no blockers or degraded items']]} />
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="AI 会话" subtitle="只展示当前账号 ticket 可见范围内的 livekit_ai_agent 会话。" />
            <CardBody>
              <DataTable
                columns={['Session', 'Status', 'AI', 'Turns', 'Started', 'Events', 'Action']}
                rows={sessionRows(sessionsData, activeSessionId, setSelectedSessionId, setPendingForceEnd, canForceEnd, forceEnd.isPending)}
              />
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="会话事件" subtitle={selectedSession ? `${selectedSession.public_id} · ticket #${selectedSession.ticket_id}` : '选择一个会话查看事件'} />
            <CardBody>
              <DataTable columns={['ID', '事件', '摘要', '时间']} rows={eventRows(events.data?.events ?? [])} />
            </CardBody>
          </Card>
        </div>

        {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
        <ConfirmDialog
          open={pendingForceEnd !== null}
          title="Force end WebCall AI session?"
          description={pendingForceEnd ? `确认强制结束 ${pendingForceEnd.public_id}？` : ''}
          consequence="后端会再次校验 runtime.manage 与 webcall.voice.end，并写入 voice/webcall_ai 事件。"
          confirmLabel="Force end"
          tone="danger"
          pending={forceEnd.isPending}
          onCancel={() => setPendingForceEnd(null)}
          onConfirm={() => {
            if (!pendingForceEnd) return
            forceEnd.mutate(pendingForceEnd.public_id)
            setPendingForceEnd(null)
          }}
        />
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall-ai-monitor',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebCallAIMonitorPage,
})
