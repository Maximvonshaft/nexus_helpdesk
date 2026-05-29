import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { CaseDetail, WebchatConversation, WebchatHandoffRequest, WebchatThread } from '@/lib/types'
import type { WebchatVoiceIncomingSession } from '@/lib/webchatVoiceTypes'
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
import { CAPABILITIES, canAccess, routeAccess } from '@/lib/rbac'
import { canForceWebchatHandoff } from '@/lib/access'
import { formatDateTime, labelize, marketLabel, sanitizeDisplayText, statusTone } from '@/lib/format'

type QueueSource = 'voice' | 'handoff' | 'conversation'
type HandoffView = 'requested' | 'mine' | 'ai_active'

type WorkbenchRow = {
  key: string
  ticketId: number
  ticketNo?: string | null
  title?: string | null
  visitorLabel?: string | null
  origin?: string | null
  pageUrl?: string | null
  voiceSessionId?: string | null
  handoffRequestId?: number | null
  handoffStatus?: string | null
  aiStatus?: string | null
  status?: string | null
  source: QueueSource
  priority: number
}

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

function visitorLabelFromConversation(item?: WebchatConversation | null) {
  if (!item) return 'Anonymous visitor'
  return item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor'
}

function visitorLabelFromHandoff(item?: WebchatHandoffRequest | null) {
  if (!item) return 'Anonymous visitor'
  return item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor'
}

function visitorLabelFromVoice(item?: WebchatVoiceIncomingSession | null) {
  if (!item) return 'Anonymous visitor'
  return item.visitor_label || 'Anonymous visitor'
}

function normalized(value?: string | null) {
  return String(value || '').trim().toLowerCase()
}

function mergeRow(map: Map<number, WorkbenchRow>, next: WorkbenchRow) {
  const existing = map.get(next.ticketId)
  if (!existing) {
    map.set(next.ticketId, next)
    return
  }
  map.set(next.ticketId, {
    ...next,
    ...existing,
    key: existing.priority <= next.priority ? existing.key : next.key,
    source: existing.priority <= next.priority ? existing.source : next.source,
    priority: Math.min(existing.priority, next.priority),
    voiceSessionId: existing.voiceSessionId || next.voiceSessionId,
    handoffRequestId: existing.handoffRequestId || next.handoffRequestId,
    handoffStatus: existing.handoffStatus || next.handoffStatus,
    aiStatus: existing.aiStatus || next.aiStatus,
    status: existing.status || next.status,
  })
}

function queueRows(
  voiceItems: WebchatVoiceIncomingSession[],
  handoffItems: WebchatHandoffRequest[],
  conversations: WebchatConversation[],
) {
  const map = new Map<number, WorkbenchRow>()
  for (const item of voiceItems) {
    mergeRow(map, {
      key: `voice-${item.voice_session_id}`,
      ticketId: item.ticket_id,
      ticketNo: item.ticket_no,
      title: item.ticket_title,
      visitorLabel: visitorLabelFromVoice(item),
      origin: item.origin,
      pageUrl: item.page_url,
      voiceSessionId: item.voice_session_id,
      status: item.status,
      source: 'voice',
      priority: 0,
    })
  }
  for (const item of handoffItems) {
    mergeRow(map, {
      key: `handoff-${item.id ?? item.ticket_id}`,
      ticketId: item.ticket_id,
      ticketNo: item.ticket_no,
      title: item.title,
      visitorLabel: visitorLabelFromHandoff(item),
      origin: item.origin,
      handoffRequestId: item.id,
      handoffStatus: item.status,
      aiStatus: item.ai_status,
      source: 'handoff',
      priority: item.status === 'requested' ? 1 : 2,
    })
  }
  for (const item of conversations) {
    mergeRow(map, {
      key: `conversation-${item.conversation_id}`,
      ticketId: item.ticket_id,
      ticketNo: item.ticket_no,
      title: item.title,
      visitorLabel: visitorLabelFromConversation(item),
      origin: item.origin,
      pageUrl: item.page_url,
      handoffRequestId: item.current_handoff_request_id,
      handoffStatus: item.handoff_status,
      aiStatus: item.ai_status,
      status: item.status,
      source: 'conversation',
      priority: item.needs_human || item.ai_pending ? 3 : 4,
    })
  }
  return [...map.values()].sort((a, b) => a.priority - b.priority || a.ticketId - b.ticketId)
}

function sourceBadge(row: WorkbenchRow) {
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

function IdentityPanel({ thread, detail, row }: { thread?: WebchatThread; detail?: CaseDetail; row?: WorkbenchRow | null }) {
  const visitorName = thread?.visitor?.name || row?.visitorLabel || null
  const visitorEmail = thread?.visitor?.email || null
  const visitorPhone = thread?.visitor?.phone || null
  const ticketName = detail?.customer_name || detail?.customer?.name || null
  const ticketEmail = detail?.customer?.email || (detail?.preferred_reply_contact?.includes('@') ? detail.preferred_reply_contact : null)
  const ticketPhone = detail?.customer?.phone || (!detail?.preferred_reply_contact?.includes('@') ? detail?.preferred_reply_contact : null)
  const nameMatch = Boolean(visitorName && ticketName && normalized(visitorName) === normalized(ticketName))
  const emailMatch = Boolean(visitorEmail && ticketEmail && normalized(visitorEmail) === normalized(ticketEmail))
  const phoneMatch = Boolean(visitorPhone && ticketPhone && normalized(visitorPhone) === normalized(ticketPhone))
  const verified = nameMatch || emailMatch || phoneMatch

  return (
    <Card data-testid="webcall-identity-verification">
      <CardHeader title="Customer Profile / Identity" subtitle="来自 WebChat thread 与 ticket summary 的客户资料核对。" />
      <CardBody>
        <div className="badges">
          <Badge tone={verified ? 'success' : 'warning'}>{verified ? '身份线索匹配' : '需要人工核对'}</Badge>
          <Badge>{emailMatch ? 'email match' : phoneMatch ? 'phone match' : nameMatch ? 'name match' : 'no direct match'}</Badge>
        </div>
        <div className="kv-grid">
          <DetailField label="访客姓名" value={visitorName} />
          <DetailField label="工单客户" value={ticketName} />
          <DetailField label="访客 Email" value={visitorEmail} />
          <DetailField label="工单 Email" value={ticketEmail} />
          <DetailField label="访客电话" value={visitorPhone} />
          <DetailField label="工单电话" value={ticketPhone} />
          <DetailField label="运单号" value={detail?.tracking_number} />
          <DetailField label="市场" value={marketLabel(detail?.market_code, detail?.country_code)} />
        </div>
      </CardBody>
    </Card>
  )
}

function AISuggestionsPanel({
  thread,
  detail,
  handoff,
}: {
  thread?: WebchatThread
  detail?: CaseDetail
  handoff?: WebchatHandoffRequest | null
}) {
  const latestAiTurn = (thread?.ai_turns ?? []).slice(-1)[0]
  const suggestions = [
    { label: 'Recommended action', text: handoff?.recommended_agent_action || thread?.required_action || detail?.required_action },
    { label: 'Missing information', text: detail?.missing_fields },
    { label: 'Customer update', text: detail?.customer_update },
    { label: 'AI summary', text: detail?.ai_summary },
    { label: 'Latest AI turn', text: latestAiTurn ? `${latestAiTurn.status}${latestAiTurn.reply_source ? ` / ${latestAiTurn.reply_source}` : ''}${latestAiTurn.fallback_reason ? ` / ${latestAiTurn.fallback_reason}` : ''}` : null },
  ].filter((item) => item.text)

  return (
    <Card data-testid="webcall-ai-suggestions">
      <CardHeader title="AI Suggestions" subtitle="使用工单提炼、WebChat AI turn 与 handoff 推荐动作。" />
      <CardBody>
        {suggestions.length ? (
          <div className="stack compact">
            {suggestions.map((item) => (
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
  const canOpenDemo = canAccess(session.data, routeAccess['/webcall-ai-demo'])
  const canForceTakeover = canForceWebchatHandoff(session.data)
  const canViewRequestedHandoff = canAccess(session.data, { allOf: [CAPABILITIES.webchatHandoffAccept] })
  const canMonitorAiHandoff = canAccess(session.data, { allOf: [CAPABILITIES.webchatConversationMonitorAi] })
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [handoffView, setHandoffView] = useState<HandoffView>('requested')
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [confirm, setConfirm] = useState<ConfirmAction | null>(null)
  const handoffTabs = useMemo(() => {
    const tabs: Array<{ key: HandoffView; label: string }> = []
    if (canViewRequestedHandoff) {
      tabs.push({ key: 'requested', label: 'Requested' }, { key: 'mine', label: 'Mine' })
    }
    if (canMonitorAiHandoff) tabs.push({ key: 'ai_active', label: 'AI Active' })
    return tabs
  }, [canMonitorAiHandoff, canViewRequestedHandoff])
  const activeHandoffView = handoffTabs.some((tab) => tab.key === handoffView) ? handoffView : handoffTabs[0]?.key

  const voiceQueue = useQuery({
    queryKey: ['webcallWorkbenchVoiceQueue'],
    queryFn: ({ signal }) => api.webchatVoiceIncomingSessions({ status: 'incoming', limit: 50 }, { signal }),
    refetchInterval: 4000,
    retry: false,
  })

  const handoffQueue = useQuery({
    queryKey: ['webcallWorkbenchHandoffQueue', activeHandoffView],
    queryFn: ({ signal }) => api.webchatHandoffQueue({ view: activeHandoffView || 'requested', limit: 80 }, { signal }),
    enabled: !!activeHandoffView,
    refetchInterval: 8000,
    retry: false,
  })

  const conversations = useQuery({
    queryKey: ['webcallWorkbenchConversations'],
    queryFn: ({ signal }) => api.webchatConversations({ signal }),
    refetchInterval: 10000,
    retry: false,
  })

  const demoStatus = useQuery({
    queryKey: ['webcallAIDemoStatus'],
    queryFn: api.webcallAIDemoStatus,
    enabled: canOpenDemo,
    refetchInterval: 30000,
    retry: false,
  })

  const rows = useMemo(
    () => queueRows(voiceQueue.data?.items ?? [], handoffQueue.data?.items ?? [], conversations.data ?? []),
    [conversations.data, handoffQueue.data?.items, voiceQueue.data?.items],
  )

  useEffect(() => {
    if (activeHandoffView && activeHandoffView !== handoffView) setHandoffView(activeHandoffView)
  }, [activeHandoffView, handoffView])

  useEffect(() => {
    if (!selectedTicketId && rows.length) setSelectedTicketId(rows[0].ticketId)
  }, [rows, selectedTicketId])

  const selectedRow = useMemo(
    () => rows.find((row) => row.ticketId === selectedTicketId) ?? null,
    [rows, selectedTicketId],
  )

  const thread = useQuery({
    queryKey: ['webchatThread', selectedTicketId, 'webcall-workbench'],
    queryFn: ({ signal }) => api.webchatThread(selectedTicketId as number, { signal }),
    enabled: !!selectedTicketId,
    refetchInterval: 6000,
    retry: false,
  })

  const caseDetail = useQuery({
    queryKey: ['caseDetail', selectedTicketId, 'webcall-workbench'],
    queryFn: () => api.caseDetail(selectedTicketId as number),
    enabled: !!selectedTicketId,
    refetchInterval: 10000,
    retry: false,
  })

  const timeline = useQuery({
    queryKey: ['ticketTimeline', selectedTicketId, 'webcall-workbench'],
    queryFn: () => api.ticketTimeline(selectedTicketId as number, { limit: 20 }),
    enabled: !!selectedTicketId,
    refetchInterval: 10000,
    retry: false,
  })

  const selectedHandoff = useMemo(() => {
    return thread.data?.handoff
      || (handoffQueue.data?.items ?? []).find((item) => item.ticket_id === selectedTicketId)
      || null
  }, [handoffQueue.data?.items, selectedTicketId, thread.data?.handoff])

  async function refreshWorkbench(ticketId = selectedTicketId) {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['webcallWorkbenchVoiceQueue'] }),
      client.invalidateQueries({ queryKey: ['webcallWorkbenchHandoffQueue'] }),
      client.invalidateQueries({ queryKey: ['webcallWorkbenchConversations'] }),
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
  const showForceTakeover = Boolean(selectedTicketId && (thread.data?.ai_pending || thread.data?.ai_status || selectedRow?.aiStatus))
  const allowForceTakeover = Boolean(canForceTakeover && (selectedHandoff?.can_force_takeover ?? true))

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/webcall']}>
        <PageHeader
          eyebrow="WebCall"
          title="WebCall Operator Workbench"
          description="来电队列、客户身份、AI 建议、handoff 和通话动作集中处理。"
          actions={<Button variant="secondary" onClick={() => void refreshWorkbench()} disabled={voiceQueue.isFetching || handoffQueue.isFetching || conversations.isFetching}>刷新</Button>}
        />

        <div className="grid two-column" data-testid="webcall-operator-workbench">
          <Card>
            <CardHeader title="WebCall Queue" subtitle="Incoming voice, handoff and WebChat context." />
            <CardBody>
              <div className="button-row" data-testid="webcall-handoff-capability-tabs">
                {handoffTabs.map((tab) => (
                  <Button key={tab.key} variant={activeHandoffView === tab.key ? 'primary' : 'secondary'} onClick={() => setHandoffView(tab.key)}>
                    {tab.label}
                  </Button>
                ))}
              </div>
              {!handoffTabs.length ? <p className="section-subtitle">当前账号没有 WebChat handoff queue 权限；仍可处理已授权的 WebCall 语音队列。</p> : null}
              {voiceQueue.isLoading || handoffQueue.isLoading || conversations.isLoading ? <Skeleton lines={5} /> : null}
              {voiceQueue.isError || handoffQueue.isError || conversations.isError ? <EmptyState title="无法加载 WebCall 工作队列" description="请刷新或检查当前账号的 WebCall/WebChat 权限。" /> : null}
              {!rows.length && !voiceQueue.isLoading && !handoffQueue.isLoading && !conversations.isLoading ? <EmptyState text="暂无 WebCall 或 handoff 队列项。" /> : null}
              <div className="stack compact">
                {rows.map((row) => (
                  <button
                    key={row.key}
                    className={`queue-card ${selectedTicketId === row.ticketId ? 'selected' : ''}`}
                    onClick={() => setSelectedTicketId(row.ticketId)}
                  >
                    <div className="badges">
                      {sourceBadge(row)}
                      {row.status ? <Badge tone={statusTone(row.status)}>{labelize(row.status)}</Badge> : null}
                      {row.handoffStatus ? <Badge>{labelize(row.handoffStatus)}</Badge> : null}
                    </div>
                    <div className="queue-card-title">{valueOrDash(row.ticketNo || `#${row.ticketId}`)} · {valueOrDash(row.title || row.voiceSessionId)}</div>
                    <div className="queue-card-meta">{valueOrDash(row.visitorLabel)} · {valueOrDash(row.origin)} · {valueOrDash(row.pageUrl)}</div>
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
                    <DetailField label="Ticket" value={caseDetail.data?.title || selectedRow?.title} />
                    <DetailField label="Ticket ID" value={selectedTicketId} />
                    <DetailField label="Ticket No" value={selectedRow?.ticketNo || (caseDetail.data ? `#${caseDetail.data.id}` : null)} />
                    <DetailField label="Status" value={caseDetail.data?.status || selectedRow?.status} />
                    <DetailField label="Priority" value={caseDetail.data?.priority} />
                    <DetailField label="Conversation" value={thread.data?.conversation_id} />
                    <DetailField label="Voice session" value={selectedRow?.voiceSessionId} />
                    <DetailField label="Preferred channel" value={caseDetail.data?.preferred_reply_channel} />
                  </div>
                ) : null}
              </CardBody>
            </Card>

            <IdentityPanel thread={thread.data} detail={caseDetail.data} row={selectedRow} />

            <Card data-testid="webcall-handoff-actions">
              <CardHeader title="Handoff Actions" subtitle="调用 WebChat handoff 真实 API。" />
              <CardBody>
                <div className="badges">
                  <Badge>{selectedHandoff ? labelize(selectedHandoff.status) : 'no handoff'}</Badge>
                  {thread.data?.ai_status ? <Badge tone="warning">AI {labelize(thread.data.ai_status)}</Badge> : null}
                  {thread.data?.ai_suspended ? <Badge tone="success">AI suspended</Badge> : null}
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

            <AISuggestionsPanel thread={thread.data} detail={caseDetail.data} handoff={selectedHandoff} />

            <AgentWebCallPanel
              ticketId={selectedTicketId}
              ticketNo={selectedRow?.ticketNo}
              conversationId={thread.data?.conversation_id}
              visitorLabel={selectedRow?.visitorLabel || thread.data?.visitor?.name}
              onSelectTicket={setSelectedTicketId}
              onActivity={() => void refreshWorkbench()}
            />

            <Card data-testid="webcall-demo-shape">
              <CardHeader title="WebCall AI Demo" subtitle="内部语音 AI 沙盒状态。" />
              <CardBody>
                {canOpenDemo ? (
                  <>
                    {demoStatus.isLoading ? <Skeleton lines={2} /> : null}
                    {demoStatus.isError ? <EmptyState text="无法读取 WebCall AI demo 状态。" /> : null}
                    {demoStatus.data ? (
                      <div className="badges">
                        <Badge tone={demoStatus.data.ok ? 'success' : 'danger'}>{demoStatus.data.status}</Badge>
                        <Badge>{demoStatus.data.enabled ? 'enabled' : 'disabled'}</Badge>
                        <Badge>{demoStatus.data.demo_mode}</Badge>
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
                {timeline.isLoading || thread.isLoading || caseDetail.isLoading ? <Skeleton lines={3} /> : null}
                <Section title="Ticket timeline">
                  {(timeline.data?.items ?? []).slice(0, 8).map((item, index) => (
                    <div className="message" data-role="agent" key={String(item.id || index)}>
                      <div className="message-head">
                        <strong>{labelize(String(item.source_type || item.kind || 'timeline'))}</strong>
                        <span>{formatDateTime(String(item.created_at || ''))}</span>
                      </div>
                      <div>{sanitizeDisplayText(String(item.body || item.summary || item.event_type || item.id || ''))}</div>
                    </div>
                  ))}
                  {!(timeline.data?.items ?? []).length && !timeline.isLoading ? <EmptyState text="暂无 timeline 证据。" /> : null}
                </Section>
                <Section title="WebChat actions">
                  {(thread.data?.actions ?? []).slice(-6).map((action) => (
                    <div className="message" data-role="agent" key={action.id}>
                      <div className="message-head"><strong>{labelize(action.action_type)}</strong><span>{formatDateTime(action.created_at)}</span></div>
                      <div>{labelize(action.status)} · {sanitizeDisplayText(action.submitted_by)}</div>
                    </div>
                  ))}
                  {!(thread.data?.actions ?? []).length && !thread.isLoading ? <EmptyState text="暂无 WebChat action audit。" /> : null}
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
