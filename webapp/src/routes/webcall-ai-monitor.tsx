import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useSession } from '@/hooks/useAuth'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Select } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { Toast } from '@/components/ui/Toast'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { actionAccess, canAccess, routeAccess } from '@/lib/rbac'
import type { BadgeTone, WebCallAIAdminEvent, WebCallAIAdminSession } from '@/lib/types'

const statusOptions = ['active', 'all', 'waiting_for_worker', 'claimed', 'joined', 'listening', 'thinking', 'speaking', 'handoff_requested', 'failed', 'ended'] as const

function monitorTone(value?: string | null): BadgeTone {
  const normalized = String(value || '').toLowerCase()
  if (['ready', 'ready_for_internal_smoke', 'ok', 'joined', 'listening', 'speaking', 'active'].includes(normalized)) return 'success'
  if (['blocked', 'failed', 'kill_switch'].includes(normalized)) return 'danger'
  if (['degraded', 'waiting_for_worker', 'claimed', 'thinking', 'handoff_requested'].includes(normalized)) return 'warning'
  return 'default'
}

function payloadPreview(event: WebCallAIAdminEvent) {
  const payload = event.payload || {}
  const text = payload.text_redacted || payload.reason || payload.status || payload.tool || payload.voice_session_id || JSON.stringify(payload)
  return sanitizeDisplayText(String(text)).slice(0, 220)
}

function sessionRows(items: WebCallAIAdminSession[], selectedId: string | null, select: (id: string) => void, forceEnd: (id: string) => void, pending: boolean, canForceEnd: boolean) {
  return items.map((item) => [
    <button type="button" className="link-button" onClick={() => select(item.public_id)} aria-pressed={selectedId === item.public_id}>{sanitizeDisplayText(item.public_id)}</button>,
    <div className="stack compact"><Badge tone={monitorTone(item.status)}>{labelize(item.status)}</Badge><span className="section-subtitle">AI {sanitizeDisplayText(item.ai_agent_status)}</span></div>,
    sanitizeDisplayText(item.provider),
    sanitizeDisplayText(item.room_name),
    String(item.ticket_id),
    String(item.ai_turn_count ?? 0),
    formatDateTime(item.started_at || item.expires_at),
    ['ended', 'failed', 'cancelled', 'missed'].includes(item.status)
      ? <span className="section-subtitle">已结束</span>
      : canForceEnd
        ? <Button variant="danger" disabled={pending} onClick={() => forceEnd(item.public_id)}>强制结束</Button>
        : <span className="section-subtitle">缺少结束权限</span>,
  ])
}

function eventRows(items: WebCallAIAdminEvent[]) {
  return items.slice(-12).reverse().map((item) => [
    sanitizeDisplayText(item.event_type),
    payloadPreview(item),
    formatDateTime(item.created_at),
  ])
}

function WebCallAIMonitorWorkbench() {
  const queryClient = useQueryClient()
  const session = useSession()
  const [status, setStatus] = useState<(typeof statusOptions)[number]>('active')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [confirmForceEnd, setConfirmForceEnd] = useState<string | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const health = useQuery({ queryKey: ['webcallAIMonitorHealth'], queryFn: api.webcallAIMonitorHealth, refetchInterval: 15000, retry: false })
  const sessions = useQuery({
    queryKey: ['webcallAIMonitorSessions', status],
    queryFn: ({ signal }) => api.webcallAIMonitorSessions({ status, limit: 80 }, { signal }),
    refetchInterval: 8000,
    retry: false,
  })
  const selected = useMemo(() => (sessions.data?.items ?? []).find((item) => item.public_id === selectedId) ?? null, [selectedId, sessions.data?.items])
  const events = useQuery({
    queryKey: ['webcallAIMonitorEvents', selectedId],
    queryFn: ({ signal }) => api.webcallAIMonitorEvents(selectedId as string, { signal }),
    enabled: !!selectedId,
    refetchInterval: 5000,
    retry: false,
  })

  useEffect(() => {
    if (!selectedId && sessions.data?.items?.length) setSelectedId(sessions.data.items[0].public_id)
  }, [selectedId, sessions.data?.items])

  async function refreshMonitor() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['webcallAIMonitorHealth'] }),
      queryClient.invalidateQueries({ queryKey: ['webcallAIMonitorSessions'] }),
      queryClient.invalidateQueries({ queryKey: ['webcallAIMonitorEvents'] }),
    ])
  }

  const forceEnd = useMutation({
    mutationFn: api.webcallAIMonitorForceEnd,
    onSuccess: async () => {
      setToast({ message: 'WebCall AI session 已强制结束', tone: 'success' })
      await refreshMonitor()
    },
    onError: (err: Error) => setToast({ message: err.message || '强制结束失败', tone: 'danger' }),
  })

  const readiness = health.data?.readiness
  const blockers = readiness?.blockers ?? []
  const degraded = readiness?.degraded ?? []
  const canForceEnd = canAccess(session.data, actionAccess.endWebcallVoice)

  return (
    <>
        <PageHeader
          eyebrow="WebCall AI"
          title="WebCall AI Monitor"
          description="AI 通话健康、LiveKit/provider readiness、会话事件和 force-end 控制。"
          actions={<Button variant="secondary" onClick={() => void refreshMonitor()}>刷新</Button>}
        />

        <div className="metrics-grid metrics-grid-wide" data-testid="webcall-ai-monitor-workbench">
          <Card className="metric"><div className="metric-label">Runtime</div><div className="metric-value">{sanitizeDisplayText(health.data?.status)}</div><Badge tone={monitorTone(health.data?.smoke_status)}>{sanitizeDisplayText(health.data?.smoke_status)}</Badge></Card>
          <Card className="metric"><div className="metric-label">Active sessions</div><div className="metric-value">{health.data?.active_sessions ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">Failed sessions</div><div className="metric-value">{health.data?.failed_sessions ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">Stale leases</div><div className="metric-value">{health.data?.stale_leases ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">Kill switch</div><div className="metric-value">{health.data?.kill_switch ? 'ON' : 'OFF'}</div></Card>
          <Card className="metric"><div className="metric-label">Last heartbeat</div><div className="metric-value">{formatDateTime(health.data?.last_heartbeat)}</div></Card>
        </div>

        <div className="page-grid split-grid-wide">
          <Card>
            <CardHeader title="AI Health / Readiness" subtitle="来自真实 /api/admin/webcall-ai/health。" />
            <CardBody>
              {health.isError ? <EmptyState title="无法加载 WebCall AI health" description={(health.error as Error).message} /> : null}
              <DataTable
                columns={['项目', '值']}
                loading={health.isLoading}
                rows={[
                  ['Agent enabled', health.data?.agent_enabled ? '是' : '否'],
                  ['Provider profile', sanitizeDisplayText(health.data?.provider_profile)],
                  ['STT / LLM / TTS', `${sanitizeDisplayText(health.data?.stt_provider)} / ${sanitizeDisplayText(health.data?.llm_provider)} / ${sanitizeDisplayText(health.data?.tts_provider)}`],
                  ['LiveKit configured', health.data?.livekit_configured ? '是' : '否'],
                  ['Provider configured', health.data?.provider_configured ? '是' : '否'],
                  ['Tracking bridge', health.data?.tracking_bridge_configured ? '是' : '否'],
                  ['Raw audio persistence', health.data?.raw_audio_persistence ? 'ON' : 'OFF'],
                  ['Dangerous write actions', health.data?.dangerous_write_actions_enabled ? 'ON' : 'OFF'],
                  ['Blockers', blockers.length ? blockers.join(', ') : '—'],
                  ['Degraded', degraded.length ? degraded.join(', ') : '—'],
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Session Events" subtitle={selected ? selected.public_id : '请选择一个会话'} />
            <CardBody>
              {!selected ? <EmptyState text="当前没有可查看的 WebCall AI session。" /> : null}
              {events.isError ? <EmptyState title="无法加载 session events" description={(events.error as Error).message} /> : null}
              <DataTable columns={['Event', 'Payload', 'Created']} rows={eventRows(events.data?.events ?? [])} loading={events.isLoading && !!selectedId} />
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader title="AI Sessions" subtitle="只显示 livekit_ai_agent 模式会话；force-end 走后端 runtime.manage 与 ticket visibility。" />
          <CardBody>
            <div className="form-grid">
              <Field label="状态">
                <Select value={status} onChange={(event) => { setStatus(event.target.value as (typeof statusOptions)[number]); setSelectedId(null) }}>
                  {statusOptions.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}
                </Select>
              </Field>
            </div>
            <div style={{ marginTop: 12 }}>
              {sessions.isError ? <EmptyState title="无法加载 WebCall AI sessions" description={(sessions.error as Error).message} /> : null}
              <DataTable
                columns={['Session', 'Status', 'Provider', 'Room', 'Ticket', 'Turns', 'Started / expires', 'Action']}
                rows={sessionRows(sessions.data?.items ?? [], selectedId, setSelectedId, setConfirmForceEnd, forceEnd.isPending, canForceEnd)}
                loading={sessions.isLoading}
              />
            </div>
          </CardBody>
        </Card>

        <ConfirmDialog
          open={!!confirmForceEnd}
          title="确认强制结束 WebCall AI session？"
          description={confirmForceEnd || ''}
          consequence="该动作会结束 AI 语音会话并写入 webcall_ai.session.ended 事件，仅限 runtime.manage。"
          confirmLabel="强制结束"
          tone="danger"
          pending={forceEnd.isPending}
          onCancel={() => setConfirmForceEnd(null)}
          onConfirm={() => {
            if (!confirmForceEnd) return
            forceEnd.mutate(confirmForceEnd)
            setConfirmForceEnd(null)
          }}
        />
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </>
  )
}

function WebCallAIMonitorPage() {
  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/webcall-ai-monitor']}>
        <WebCallAIMonitorWorkbench />
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
