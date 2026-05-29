import { useEffect, useState, type ReactNode } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type {
  CaseDetail,
  WebCallAISuggestion,
  WebCallIdentityVerification,
  WebCallOperatorRow,
  WebchatHandoffRequest,
  WebchatThread,
} from '@/lib/types'
import { AgentWebCallPanel } from '@/components/webcall/AgentWebCallPanel'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useSession } from '@/hooks/useAuth'
import { canAccess, routeAccess } from '@/lib/rbac'
import { canForceWebchatHandoff } from '@/lib/access'
import { formatDateTime, labelize, marketLabel, sanitizeDisplayText, statusTone } from '@/lib/format'

type HandoffAction =
  | { type: 'accept'; requestId: number }
  | { type: 'decline'; requestId: number }
  | { type: 'release'; requestId: number }
  | { type: 'resume'; requestId: number }
  | { type: 'force'; ticketId: number }

type ConfirmAction = {
  title: string
  description: string
  consequence?: string
  confirmLabel: string
  tone?: 'default' | 'danger'
  run: () => void
}

function valueOrDash(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '-'
  return sanitizeDisplayText(String(value))
}

function normalized(value?: string | null) {
  return String(value || '').trim().toLowerCase()
}

function sourceBadge(row: WebCallOperatorRow) {
  if (row.source === 'voice') return <Badge tone="warning">Incoming WebCall</Badge>
  if (row.source === 'handoff') return <Badge tone="success">Handoff</Badge>
  return <Badge>WebChat</Badge>
}

function DetailField({ label, value }: { label: string; value?: string | number | null }) {
  return <div className="kv"><label>{label}</label><div>{valueOrDash(value)}</div></div>
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="stack compact"><div className="section-title">{title}</div>{children}</section>
}

function IdentityPanel({
  thread,
  detail,
  row,
  identity,
}: {
  thread?: WebchatThread | null
  detail?: CaseDetail | null
  row?: WebCallOperatorRow | null
  identity?: WebCallIdentityVerification | null
}) {
  const visitorName = identity?.visitor.name || thread?.visitor?.name || row?.visitor_label || null
  const visitorEmail = identity?.visitor.email || thread?.visitor?.email || null
  const visitorPhone = identity?.visitor.phone || thread?.visitor?.phone || null
  const ticketName = identity?.ticket_customer.name || detail?.customer_name || detail?.customer?.name || null
  const ticketEmail = identity?.ticket_customer.email || detail?.customer?.email || (detail?.preferred_reply_contact?.includes('@') ? detail.preferred_reply_contact : null)
  const ticketPhone = identity?.ticket_customer.phone || detail?.customer?.phone || (!detail?.preferred_reply_contact?.includes('@') ? detail?.preferred_reply_contact : null)
  const nameMatch = Boolean(visitorName && ticketName && normalized(visitorName) === normalized(ticketName))
  const emailMatch = Boolean(visitorEmail && ticketEmail && normalized(visitorEmail) === normalized(ticketEmail))
  const phoneMatch = Boolean(visitorPhone && ticketPhone && normalized(visitorPhone) === normalized(ticketPhone))
  const verified = identity ? identity.verification_status === 'matched' : nameMatch || emailMatch || phoneMatch
  const matchBasis = identity?.match_basis?.length ? identity.match_basis.join(', ') : emailMatch ? 'email match' : phoneMatch ? 'phone match' : nameMatch ? 'name match' : 'no direct match'

  return (
    <Card data-testid="webcall-identity-verification">
      <CardHeader title="Customer Profile / Identity" subtitle="来自 WebCall operator backend 契约的客户资料核对。" />
      <CardBody>
        <div className="badges">
          <Badge tone={verified ? 'success' : 'warning'}>{verified ? '身份线索匹配' : '需要人工核对'}</Badge>
          <Badge>{matchBasis}</Badge>
        </div>
        <div className="kv-grid">
          <DetailField label="访客姓名" value={visitorName} />
          <DetailField label="工单客户" value={ticketName} />
          <DetailField label="访客 Email" value={visitorEmail} />
          <DetailField label="工单 Email" value={ticketEmail} />
          <DetailField label="访客电话" value={visitorPhone} />
          <DetailField label="工单电话" value={ticketPhone} />
          <DetailField label="运单号" value={identity?.tracking_number || detail?.tracking_number} />
          <DetailField label="市场" value={marketLabel(identity?.market_code || detail?.market_code, identity?.country_code || detail?.country_code)} />
        </div>
      </CardBody>
    </Card>
  )
}

function AISuggestionsPanel({
  thread,
  detail,
  handoff,
  suggestions,
}: {
  thread?: WebchatThread | null
  detail?: CaseDetail | null
  handoff?: WebchatHandoffRequest | null
  suggestions?: WebCallAISuggestion[] | null
}) {
  const latestAiTurn = (thread?.ai_turns ?? []).slice(-1)[0]
  const fallbackSuggestions = [
    { label: 'Recommended action', text: handoff?.recommended_agent_action || thread?.required_action || detail?.required_action },
    { label: 'Missing information', text: detail?.missing_fields },
    { label: 'Customer update', text: detail?.customer_update },
    { label: 'AI summary', text: detail?.ai_summary },
    { label: 'Latest AI turn', text: latestAiTurn ? `${latestAiTurn.status}${latestAiTurn.reply_source ? ` / ${latestAiTurn.reply_source}` : ''}${latestAiTurn.fallback_reason ? ` / ${latestAiTurn.fallback_reason}` : ''}` : null },
  ].filter((item) => item.text)
  const visibleSuggestions = suggestions?.length ? suggestions : fallbackSuggestions

  return (
    <Card data-testid="webcall-ai-suggestions">
      <CardHeader title="AI Suggestions" subtitle="使用 operator workbench 后端契约汇总工单提炼、WebChat AI turn 与 handoff 推荐动作。" />
      <CardBody>
        {visibleSuggestions.length ? (
          <div className="stack compact">
            {visibleSuggestions.map((item) => (
              <div className="message" data-role="agent" key={item.label}>
                <div className="message-head"><strong>{item.label}</strong></div>
                <div>{sanitizeDisplayText(String(item.text))}</div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState text="暂无 AI 建议；请先查看客户上下文并人工处理。" />
        )}
      </CardBody>
    </Card>
  )
}

function WebCallOperatorWorkbenchPage() {
  const client = useQueryClient()
  const session = useSession()
  const navigate = useNavigate()
  const canOpenDemoByRole = canAccess(session.data, routeAccess['/webcall-ai-demo'])
  const canForceTakeover = canForceWebchatHandoff(session.data)
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [handoffView, setHandoffView] = useState<'requested' | 'mine' | 'ai_active'>('requested')
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [confirm, setConfirm] = useState<ConfirmAction | null>(null)

  const workbench = useQuery({
    queryKey: ['webcallOperatorWorkbench', handoffView, selectedTicketId],
    queryFn: ({ signal }) => api.webcallOperatorWorkbench({
      view: handoffView,
      voice_status: 'incoming',
      ticket_id: selectedTicketId,
      limit: 80,
    }, { signal }),
    refetchInterval: 6000,
    retry: false,
  })

  const rows = workbench.data?.rows ?? []

  useEffect(() => {
    if (!selectedTicketId && workbench.data?.selected_ticket_id) setSelectedTicketId(workbench.data.selected_ticket_id)
  }, [selectedTicketId, workbench.data?.selected_ticket_id])

  const selectedRow = workbench.data?.selected?.row || rows.find((row) => row.ticket_id === selectedTicketId) || null
  const selected = workbench.data?.selected ?? null
  const threadData = selected?.thread ?? null
  const caseDetailData = selected?.ticket ?? null
  const selectedHandoff = selected?.handoff ?? null
  const timelineItems = selected?.timeline?.items ?? []
  const canOpenDemo = Boolean(workbench.data?.demo.visible ?? canOpenDemoByRole)
  const demoStatus = workbench.data?.demo.status as { ok?: boolean; status?: string; enabled?: boolean; demo_mode?: string } | null | undefined

  async function refreshWorkbench(ticketId = selectedTicketId) {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['webcallOperatorWorkbench'] }),
      client.invalidateQueries({ queryKey: ['webchatVoiceOperationalQueue'] }),
      ticketId ? client.invalidateQueries({ queryKey: ['webchatThread', ticketId] }) : Promise.resolve(),
      ticketId ? client.invalidateQueries({ queryKey: ['caseDetail', ticketId] }) : Promise.resolve(),
      ticketId ? client.invalidateQueries({ queryKey: ['ticketTimeline', ticketId] }) : Promise.resolve(),
      ticketId ? client.invalidateQueries({ queryKey: ['webchatVoiceSessions', ticketId] }) : Promise.resolve(),
    ])
  }

  const handoffMutation = useMutation({
    mutationFn: async (action: HandoffAction) => {
      if (action.type === 'accept') return api.webchatAcceptHandoff(action.requestId, 'Accepted from WebCall operator workbench')
      if (action.type === 'decline') return api.webchatDeclineHandoff(action.requestId, { reason_code: 'webcall_operator_declined', note: 'Declined from WebCall operator workbench' })
      if (action.type === 'release') return api.webchatReleaseHandoff(action.requestId, 'Released from WebCall operator workbench')
      if (action.type === 'resume') return api.webchatResumeAi(action.requestId, 'Resumed from WebCall operator workbench')
      return api.webchatForceTakeover(action.ticketId, { reason_code: 'webcall_operator_force_takeover', note: 'Forced takeover from WebCall operator workbench' })
    },
    onSuccess: async (handoff) => {
      setSelectedTicketId(handoff.ticket_id)
      setToast({ message: 'Handoff 状态已更新', tone: 'success' })
      await refreshWorkbench(handoff.ticket_id)
    },
    onError: (err: Error) => setToast({ message: err.message || 'Handoff 操作失败', tone: 'danger' }),
  })

  const handoffBusy = handoffMutation.isPending
  const canAcceptHandoff = selectedHandoff?.status === 'requested' && typeof selectedHandoff.id === 'number' && selectedHandoff.can_accept !== false
  const canDeclineHandoff = selectedHandoff?.status === 'requested' && typeof selectedHandoff.id === 'number' && selectedHandoff.can_decline !== false
  const canReleaseHandoff = selectedHandoff?.status === 'accepted' && typeof selectedHandoff.id === 'number' && selectedHandoff.can_release === true
  const canResumeAi = selectedHandoff?.can_resume_ai === true && typeof selectedHandoff.id === 'number'
  const showForceTakeover = Boolean(selectedTicketId && (threadData?.ai_pending || threadData?.ai_status || selectedRow?.ai_status))
  const allowForceTakeover = Boolean(canForceTakeover && (selectedHandoff?.can_force_takeover ?? true))

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/webcall']}>
        <PageHeader
          eyebrow="WebCall"
          title="WebCall Operator Workbench"
          description="来电队列、客户身份、AI 建议、handoff 和通话动作集中处理。"
          actions={<Button variant="secondary" onClick={() => void refreshWorkbench()} disabled={workbench.isFetching}>刷新</Button>}
        />

        <div className="grid two-column" data-testid="webcall-operator-workbench">
          <Card>
            <CardHeader title="WebCall Queue" subtitle="Incoming voice, handoff and WebChat context." />
            <CardBody>
              <div className="button-row">
                {(['requested', 'mine', 'ai_active'] as const).map((view) => (
                  <Button key={view} variant={handoffView === view ? 'primary' : 'secondary'} onClick={() => setHandoffView(view)}>
                    {labelize(view)}
                  </Button>
                ))}
              </div>
              {workbench.isLoading ? <Skeleton lines={5} /> : null}
              {workbench.isError ? <EmptyState title="无法加载 WebCall 工作队列" description="请刷新或检查当前账号的 WebCall/WebChat 权限。" /> : null}
              {!rows.length && !workbench.isLoading ? <EmptyState text="暂无 WebCall 或 handoff 队列项。" /> : null}
              <div className="stack compact">
                {rows.map((row) => (
                  <button
                    key={row.key}
                    className={`queue-card ${selectedTicketId === row.ticket_id ? 'selected' : ''}`}
                    onClick={() => setSelectedTicketId(row.ticket_id)}
                  >
                    <div className="badges">
                      {sourceBadge(row)}
                      {row.status ? <Badge tone={statusTone(row.status)}>{labelize(row.status)}</Badge> : null}
                      {row.handoff_status ? <Badge>{labelize(row.handoff_status)}</Badge> : null}
                    </div>
                    <div className="queue-card-title">{valueOrDash(row.ticket_no || `#${row.ticket_id}`)} · {valueOrDash(row.title || row.voice_session_id)}</div>
                    <div className="queue-card-meta">{valueOrDash(row.visitor_label)} · {valueOrDash(row.origin)} · {valueOrDash(row.page_url)}</div>
                  </button>
                ))}
              </div>
            </CardBody>
          </Card>

          <div className="stack">
            <Card data-testid="webcall-session-context">
              <CardHeader title="Selected WebCall Context" subtitle="Ticket, WebChat thread and backend state." />
              <CardBody>
                {!selectedTicketId ? <EmptyState text="请选择一个 WebCall 队列项。" /> : null}
                {selectedTicketId ? (
                  <div className="kv-grid">
                    <DetailField label="Ticket" value={caseDetailData?.title || selectedRow?.title} />
                    <DetailField label="Ticket ID" value={selectedTicketId} />
                    <DetailField label="Ticket No" value={selectedRow?.ticket_no || (caseDetailData ? `#${caseDetailData.id}` : null)} />
                    <DetailField label="Status" value={caseDetailData?.status || selectedRow?.status} />
                    <DetailField label="Priority" value={caseDetailData?.priority} />
                    <DetailField label="Conversation" value={threadData?.conversation_id} />
                    <DetailField label="Voice session" value={selectedRow?.voice_session_id} />
                    <DetailField label="Preferred channel" value={caseDetailData?.preferred_reply_channel} />
                  </div>
                ) : null}
              </CardBody>
            </Card>

            <IdentityPanel thread={threadData} detail={caseDetailData} row={selectedRow} identity={selected?.identity} />

            <Card data-testid="webcall-handoff-actions">
              <CardHeader title="Handoff Actions" subtitle="调用 WebChat handoff 真实 API。" />
              <CardBody>
                <div className="badges">
                  <Badge>{selectedHandoff ? labelize(selectedHandoff.status) : 'no handoff'}</Badge>
                  {threadData?.ai_status ? <Badge tone="warning">AI {labelize(threadData.ai_status)}</Badge> : null}
                  {threadData?.ai_suspended ? <Badge tone="success">AI suspended</Badge> : null}
                </div>
                <div className="button-row">
                  {canAcceptHandoff ? <Button variant="primary" disabled={handoffBusy} onClick={() => handoffMutation.mutate({ type: 'accept', requestId: selectedHandoff.id as number })}>接管</Button> : null}
                  {canDeclineHandoff ? <Button variant="secondary" disabled={handoffBusy} onClick={() => handoffMutation.mutate({ type: 'decline', requestId: selectedHandoff.id as number })}>跳过</Button> : null}
                  {canReleaseHandoff ? <Button variant="secondary" disabled={handoffBusy} onClick={() => handoffMutation.mutate({ type: 'release', requestId: selectedHandoff.id as number })}>释放回队列</Button> : null}
                  {canResumeAi ? <Button variant="secondary" disabled={handoffBusy} onClick={() => handoffMutation.mutate({ type: 'resume', requestId: selectedHandoff.id as number })}>恢复 AI</Button> : null}
                  {showForceTakeover ? (
                    <Button
                      variant="danger"
                      disabled={handoffBusy || !allowForceTakeover || !selectedTicketId}
                      onClick={() => selectedTicketId && setConfirm({
                        title: '确认强制接管？',
                        description: '该操作会暂停 AI，并取消当前未完成的 AI 回复。',
                        consequence: '只在客户等待人工接听或 AI 未正确让渡时使用。',
                        confirmLabel: '强制接管',
                        tone: 'danger',
                        run: () => handoffMutation.mutate({ type: 'force', ticketId: selectedTicketId }),
                      })}
                    >
                      强制接管
                    </Button>
                  ) : null}
                  <Button variant="secondary" onClick={() => void refreshWorkbench()} disabled={handoffBusy}>刷新上下文</Button>
                </div>
                {!selectedHandoff ? <p className="section-subtitle">当前 ticket 没有开放 handoff；如果 AI 仍在处理，可使用强制接管权限。</p> : null}
              </CardBody>
            </Card>

            <AISuggestionsPanel thread={threadData} detail={caseDetailData} handoff={selectedHandoff} suggestions={selected?.ai_suggestions} />

            <AgentWebCallPanel
              ticketId={selectedTicketId}
              ticketNo={selectedRow?.ticket_no}
              conversationId={threadData?.conversation_id}
              visitorLabel={selectedRow?.visitor_label || threadData?.visitor?.name}
              onSelectTicket={setSelectedTicketId}
              onActivity={() => void refreshWorkbench()}
            />

            <Card data-testid="webcall-demo-shape">
              <CardHeader title="WebCall AI Demo" subtitle="内部语音 AI 沙盒状态。" />
              <CardBody>
                {canOpenDemo ? (
                  <>
                    {workbench.isLoading ? <Skeleton lines={2} /> : null}
                    {!demoStatus && !workbench.isLoading ? <EmptyState text="无法读取 WebCall AI demo 状态。" /> : null}
                    {demoStatus ? (
                      <div className="badges">
                        <Badge tone={demoStatus.ok ? 'success' : 'danger'}>{demoStatus.status}</Badge>
                        <Badge>{demoStatus.enabled ? 'enabled' : 'disabled'}</Badge>
                        <Badge>{demoStatus.demo_mode}</Badge>
                      </div>
                    ) : null}
                    <div className="button-row">
                      <Button variant="secondary" onClick={() => navigate({ to: '/webcall-ai-demo' })}>打开 AI Demo 沙盒</Button>
                    </div>
                  </>
                ) : (
                  <EmptyState text="当前账号不能查看 WebCall AI demo；生产接听不依赖 demo 权限。" />
                )}
              </CardBody>
            </Card>

            <Card data-testid="webcall-timeline-audit">
              <CardHeader title="Timeline / Audit" subtitle="ticket timeline 与 WebChat action audit 回写预览。" />
              <CardBody>
                {workbench.isLoading ? <Skeleton lines={3} /> : null}
                <Section title="Ticket timeline">
                  {timelineItems.slice(0, 8).map((item, index) => (
                    <div className="message" data-role="agent" key={String(item.id || index)}>
                      <div className="message-head">
                        <strong>{labelize(String(item.source_type || item.kind || 'timeline'))}</strong>
                        <span>{formatDateTime(String(item.created_at || ''))}</span>
                      </div>
                      <div>{sanitizeDisplayText(String(item.body || item.summary || item.event_type || item.id || ''))}</div>
                    </div>
                  ))}
                  {!timelineItems.length && !workbench.isLoading ? <EmptyState text="暂无 timeline 证据。" /> : null}
                </Section>
                <Section title="WebChat actions">
                  {(threadData?.actions ?? []).slice(-6).map((action) => (
                    <div className="message" data-role="agent" key={action.id}>
                      <div className="message-head"><strong>{labelize(action.action_type)}</strong><span>{formatDateTime(action.created_at)}</span></div>
                      <div>{labelize(action.status)} · {sanitizeDisplayText(action.submitted_by)}</div>
                    </div>
                  ))}
                  {!(threadData?.actions ?? []).length && !workbench.isLoading ? <EmptyState text="暂无 WebChat action audit。" /> : null}
                </Section>
              </CardBody>
            </Card>
          </div>
        </div>

        <ConfirmDialog
          open={!!confirm}
          title={confirm?.title || ''}
          description={confirm?.description || ''}
          consequence={confirm?.consequence}
          confirmLabel={confirm?.confirmLabel}
          tone={confirm?.tone}
          pending={handoffBusy}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            confirm?.run()
            setConfirm(null)
          }}
        />
        {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebCallOperatorWorkbenchPage,
})
