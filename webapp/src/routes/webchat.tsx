import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText, statusTone } from '@/lib/format'
import { findReplyChannelCapability, isCustomerSendableReplyChannel, outboundChannelMissingText, replyPanelVisibleChannels } from '@/lib/outboundChannels'
import type { WebchatCardAction, WebchatCardPayload, WebchatHandoffRequest, WebchatMessage } from '@/lib/types'
import { webchatVoiceApi } from '@/lib/webchatVoiceApi'
import { AgentWebCallPanel } from '@/components/webcall/AgentWebCallPanel'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Textarea } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { SegmentedControl } from '@/components/ui/SegmentedControl'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'
import { useSession } from '@/hooks/useAuth'
import { canViewWebcallVoiceQueue, canViewWebchatDebug } from '@/lib/access'

function isCardPayload(payload: WebchatMessage['payload_json']): payload is WebchatCardPayload {
  return Boolean(payload && typeof payload === 'object' && 'card_type' in payload && 'actions' in payload)
}

function PayloadBlock({ payload, allowDebug }: { payload: unknown; allowDebug: boolean }) {
  const [open, setOpen] = useState(false)
  if (!allowDebug) return null
  if (!payload || typeof payload !== 'object') return null
  return (
    <div className="stack compact">
      <Button variant="secondary" onClick={() => setOpen((value) => !value)}>{open ? '收起 payload' : '查看 payload'}</Button>
      {open ? <pre className="code-block"><code>{sanitizeDisplayText(JSON.stringify(payload, null, 2))}</code></pre> : null}
    </div>
  )
}

function aiStatusTone(status?: string | null, pending?: boolean): 'default' | 'warning' | 'success' | 'danger' {
  if (!status) return 'default'
  if (status === 'completed') return 'success'
  if (status === 'failed' || status === 'timeout' || status === 'cancelled') return 'danger'
  if (pending || ['queued', 'processing', 'bridge_calling', 'fallback_generating'].includes(status)) return 'warning'
  return 'default'
}

function AIStatusBadge({ status, pending, turnId }: { status?: string | null; pending?: boolean; turnId?: number | null }) {
  const label = status || 'none'
  const suffix = turnId ? ` #${turnId}` : ''
  return <Badge tone={aiStatusTone(status, pending)}>AI {sanitizeDisplayText(label)}{suffix}</Badge>
}

function handoffTone(status?: string | null): 'default' | 'warning' | 'success' | 'danger' {
  if (status === 'accepted') return 'success'
  if (status === 'requested') return 'warning'
  if (status === 'resumed_ai' || status === 'closed') return 'default'
  if (status === 'cancelled' || status === 'expired') return 'danger'
  return 'default'
}

function HandoffQueueItem({
  item,
  selected,
  onSelect,
  onAccept,
  onDecline,
  onForce,
  busy,
}: {
  item: WebchatHandoffRequest
  selected: boolean
  onSelect: () => void
  onAccept: () => void
  onDecline: () => void
  onForce: () => void
  busy: boolean
}) {
  const canAccept = typeof item.id === 'number' && item.status === 'requested'
  const canForce = item.status === 'ai_active'
  return (
    <div
      className={`queue-card ${selected ? 'selected' : ''}`}
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') onSelect()
      }}
    >
      <div className="queue-card-top"><div className="badges">
        <Badge tone={handoffTone(item.status)}>{sanitizeDisplayText(item.status)}</Badge>
        <Badge>{sanitizeDisplayText(item.source)}</Badge>
        {item.ai_suspended ? <Badge tone="warning">AI paused</Badge> : <AIStatusBadge status={item.ai_status} pending={item.ai_pending} turnId={item.ai_turn_id} />}
      </div></div>
      <div className="queue-card-title">{sanitizeDisplayText(item.ticket_no || `#${item.ticket_id}`)} · {sanitizeDisplayText(item.title || 'WebChat handoff')}</div>
      <div className="queue-card-meta">{sanitizeDisplayText(item.reason_text || item.reason_code || item.trigger_type)}</div>
      {item.last_message?.body_text ? <div className="queue-card-meta">{sanitizeDisplayText(item.last_message.body_text)}</div> : null}
      <div className="badges" onClick={(event) => event.stopPropagation()}>
        {canAccept ? <Button variant="primary" disabled={busy} onClick={onAccept}>接管</Button> : null}
        {canAccept ? <Button variant="secondary" disabled={busy} onClick={onDecline}>跳过</Button> : null}
        {canForce ? <Button variant="danger" disabled={busy} onClick={onForce}>强制接管</Button> : null}
      </div>
    </div>
  )
}

function voiceEvidenceValue(payload: Record<string, unknown> | null | undefined, key: string) {
  const value = payload?.[key]
  if (value === null || value === undefined || value === '') return '-'
  return sanitizeDisplayText(String(value))
}

function MessageCard({ msg, allowDebug }: { msg: WebchatMessage; allowDebug: boolean }) {
  const messageType = msg.message_type || 'text'
  const cardPayload = isCardPayload(msg.payload_json) ? msg.payload_json : null
  if (messageType === 'voice_call') {
    const payload = msg.payload_json && typeof msg.payload_json === 'object' ? msg.payload_json as Record<string, unknown> : null
    return (
      <div className="message" data-role="agent" data-testid="voice-call-evidence-card">
        <div className="message-head">
          <strong>WebCall evidence · {voiceEvidenceValue(payload, 'status')}</strong>
          <span>{formatDateTime(msg.created_at)}</span>
        </div>
        <div>{sanitizeDisplayText(msg.body_text || msg.body)}</div>
        <div className="kv-grid" style={{ marginTop: 10 }}>
          <div className="kv"><label>voice_session_id</label><div>{voiceEvidenceValue(payload, 'voice_session_id')}</div></div>
          <div className="kv"><label>provider</label><div>{voiceEvidenceValue(payload, 'provider')}</div></div>
          <div className="kv"><label>accepted_by</label><div>{voiceEvidenceValue(payload, 'accepted_by')}</div></div>
          <div className="kv"><label>ended_by</label><div>{voiceEvidenceValue(payload, 'ended_by')}</div></div>
          <div className="kv"><label>ringing_duration_seconds</label><div>{voiceEvidenceValue(payload, 'ringing_duration_seconds')}</div></div>
          <div className="kv"><label>talk_duration_seconds</label><div>{voiceEvidenceValue(payload, 'talk_duration_seconds')}</div></div>
          <div className="kv"><label>total_duration_seconds</label><div>{voiceEvidenceValue(payload, 'total_duration_seconds')}</div></div>
          <div className="kv"><label>recording status</label><div>{voiceEvidenceValue(payload, 'recording_status')}</div></div>
          <div className="kv"><label>transcript status</label><div>{voiceEvidenceValue(payload, 'transcript_status')}</div></div>
          <div className="kv"><label>summary status</label><div>{voiceEvidenceValue(payload, 'summary_status')}</div></div>
        </div>
      </div>
    )
  }
  if (messageType === 'card') {
    const actions: WebchatCardAction[] = cardPayload?.actions ?? []
    return (
      <div className="message" data-role="agent">
        <div className="message-head">
          <strong>结构化卡片 · {sanitizeDisplayText(cardPayload?.card_type || 'card')}</strong>
          <span>{formatDateTime(msg.created_at)}</span>
        </div>
        <div className="stack compact">
          <div><strong>{sanitizeDisplayText(cardPayload?.title || msg.body_text || msg.body)}</strong></div>
          {cardPayload?.body ? <div>{sanitizeDisplayText(cardPayload.body)}</div> : null}
          <div className="badges">
            <Badge tone="success">{sanitizeDisplayText(msg.action_status || 'pending')}</Badge>
            {actions.map((action) => <Badge key={action.id}>{sanitizeDisplayText(action.label || action.id)}</Badge>)}
          </div>
          <PayloadBlock payload={msg.payload_json} allowDebug={allowDebug} />
        </div>
      </div>
    )
  }
  if (messageType === 'action' || msg.direction === 'action') {
    return (
      <div className="message" data-role="user">
        <div className="message-head">
          <strong>客户动作</strong>
          <span>{formatDateTime(msg.created_at)}</span>
        </div>
        <div>{sanitizeDisplayText(msg.body_text || msg.body)}</div>
        <PayloadBlock payload={msg.payload_json} allowDebug={allowDebug} />
      </div>
    )
  }
  return (
    <div className="message" data-role={msg.direction === 'visitor' ? 'user' : 'agent'}>
      <div className="message-head">
        <strong>{msg.direction === 'visitor' ? '访客' : msg.direction === 'system' ? '系统' : msg.author_label ? sanitizeDisplayText(msg.author_label) : '客服 / AI'}</strong>
        <span>{formatDateTime(msg.created_at)}</span>
      </div>
      <div>{sanitizeDisplayText(msg.body_text || msg.body)}</div>
    </div>
  )
}

function backoffMs(failures: number, baseMs: number, maxMs: number) {
  if (failures <= 0) return baseMs
  return Math.min(maxMs, baseMs * 2 ** Math.min(failures, 4))
}

function WebchatInboxPage() {
  const client = useQueryClient()
  const session = useSession()
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [lastEventId, setLastEventId] = useState(0)
  const [reply, setReply] = useState('')
  const [hasFactEvidence, setHasFactEvidence] = useState(false)
  const [confirmReview, setConfirmReview] = useState(false)
  const [handoffView, setHandoffView] = useState<'requested' | 'ai_active' | 'mine'>('requested')
  const [eventPollFailures, setEventPollFailures] = useState(0)
  const [conversationPollFailures, setConversationPollFailures] = useState(0)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const outboundCapabilities = useQuery({
    queryKey: ['outboundChannelCapabilities'],
    queryFn: api.outboundChannelCapabilities,
    refetchInterval: 30000,
    retry: false,
  })

  const conversations = useQuery({
    queryKey: ['webchatConversations'],
    queryFn: ({ signal }) => api.webchatConversations({ signal }),
    refetchInterval: backoffMs(conversationPollFailures, 10000, 60000),
    retry: false,
  })

  const incomingVoiceSessions = useQuery({
    queryKey: ['webchatVoiceIncomingSessions', 'webchat-integrated-entry'],
    queryFn: ({ signal }) => webchatVoiceApi.incomingSessions({ status: 'incoming', limit: 50 }, { signal }),
    enabled: canViewWebcallVoiceQueue(session.data),
    refetchInterval: 4000,
    retry: false,
  })
  const allowDebug = canViewWebchatDebug(session.data)

  const handoffQueue = useQuery({
    queryKey: ['webchatHandoffQueue', handoffView],
    queryFn: ({ signal }) => api.webchatHandoffQueue({ view: handoffView, limit: 50 }, { signal }),
    refetchInterval: 5000,
    retry: false,
  })

  const refreshWebchatState = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['webchatHandoffQueue'] }),
      client.invalidateQueries({ queryKey: ['webchatConversations'] }),
      client.invalidateQueries({ queryKey: ['webchatThread', selectedTicketId] }),
    ])
  }

  useEffect(() => {
    if (conversations.isSuccess) setConversationPollFailures(0)
    if (conversations.isError) setConversationPollFailures((value) => Math.min(value + 1, 6))
  }, [conversations.isSuccess, conversations.isError, conversations.dataUpdatedAt, conversations.errorUpdatedAt])

  useEffect(() => {
    if (!selectedTicketId && conversations.data?.length) {
      setSelectedTicketId(conversations.data[0].ticket_id)
    }
  }, [conversations.data, selectedTicketId])

  useEffect(() => {
    setLastEventId(0)
    setEventPollFailures(0)
  }, [selectedTicketId])

  const thread = useQuery({
    queryKey: ['webchatThread', selectedTicketId],
    queryFn: ({ signal }) => api.webchatThread(selectedTicketId as number, { signal }),
    enabled: !!selectedTicketId,
    refetchInterval: 7000,
    retry: false,
  })

  const events = useQuery({
    queryKey: ['webchatEvents', selectedTicketId, lastEventId],
    queryFn: ({ signal }) => api.webchatEvents(selectedTicketId as number, lastEventId, { signal }),
    enabled: !!selectedTicketId,
    refetchInterval: backoffMs(eventPollFailures, 2500, 30000),
    retry: false,
  })

  useEffect(() => {
    if (events.isSuccess) setEventPollFailures(0)
    if (events.isError) setEventPollFailures((value) => Math.min(value + 1, 6))
  }, [events.isSuccess, events.isError, events.dataUpdatedAt, events.errorUpdatedAt])

  useEffect(() => {
    if (!selectedTicketId || !events.data?.events?.length) return
    setLastEventId(events.data.last_event_id || events.data.events[events.data.events.length - 1].id)
    void client.invalidateQueries({ queryKey: ['webchatThread', selectedTicketId] })
    void client.invalidateQueries({ queryKey: ['webchatConversations'] })
    void client.invalidateQueries({ queryKey: ['webchatVoiceIncomingSessions'] })
  }, [client, events.data, selectedTicketId])

  const selectedConversation = useMemo(
    () => (conversations.data ?? []).find((item) => item.ticket_id === selectedTicketId),
    [conversations.data, selectedTicketId],
  )
  const incomingVoiceByTicket = useMemo(() => {
    const values = new Map<number, number>()
    for (const item of incomingVoiceSessions.data?.items ?? []) {
      values.set(item.ticket_id, (values.get(item.ticket_id) ?? 0) + 1)
    }
    return values
  }, [incomingVoiceSessions.data?.items])
  const threadData = thread.data
  const selectedHandoff = threadData?.handoff
  const handoffReplyBlocked = Boolean(
    selectedHandoff
    && !(selectedHandoff.status === 'accepted' && selectedHandoff.active_agent_id === session.data?.id),
  )
  const visibleReplyChannels = useMemo(
    () => replyPanelVisibleChannels(outboundCapabilities.data?.channels),
    [outboundCapabilities.data],
  )
  const webchatReplyChannel = useMemo(
    () => findReplyChannelCapability(outboundCapabilities.data?.channels, 'web_chat'),
    [outboundCapabilities.data],
  )
  const webchatReplyEnabled = isCustomerSendableReplyChannel(webchatReplyChannel)
  const webchatCapabilityIssue = outboundCapabilities.isError
    ? 'capability_api_unavailable'
    : webchatReplyChannel
      ? outboundChannelMissingText(webchatReplyChannel)
      : 'web_chat_capability_missing'

  const replyMutation = useMutation({
    mutationFn: async () => {
      if (!selectedTicketId) return
      if (!webchatReplyEnabled) {
        throw new Error(`WebChat outbound channel is not ready: ${webchatCapabilityIssue}`)
      }
      return api.webchatReply(selectedTicketId, {
        body: reply,
        has_fact_evidence: hasFactEvidence,
        confirm_review: confirmReview,
      })
    },
    onSuccess: async () => {
      setToast({ message: 'Webchat 回复已发送，访客端可见；该记录是 WebChat local delivery，不是外部渠道发送。', tone: 'success' })
      setReply('')
      setConfirmReview(false)
      setHasFactEvidence(false)
      await client.invalidateQueries({ queryKey: ['webchatThread', selectedTicketId] })
      await client.invalidateQueries({ queryKey: ['webchatConversations'] })
      await client.invalidateQueries({ queryKey: ['outboundChannelCapabilities'] })
    },
    onError: (err: Error) => {
      setToast({ message: err.message || '发送失败，已被安全门拦截或需要复核', tone: 'danger' })
    },
  })

  const acceptMutation = useMutation({
    mutationFn: (requestId: number) => api.webchatAcceptHandoff(requestId),
    onSuccess: async (handoff) => {
      setSelectedTicketId(handoff.ticket_id)
      setToast({ message: '已接管会话，AI 已暂停。', tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err: Error) => setToast({ message: err.message || '接管失败', tone: 'danger' }),
  })

  const declineMutation = useMutation({
    mutationFn: (requestId: number) => api.webchatDeclineHandoff(requestId, { reason_code: 'agent_skipped' }),
    onSuccess: async () => {
      setToast({ message: '已跳过；该请求仍会保留给其他客服。', tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err: Error) => setToast({ message: err.message || '跳过失败', tone: 'danger' }),
  })

  const forceMutation = useMutation({
    mutationFn: (ticketId: number) => api.webchatForceTakeover(ticketId, { reason_code: 'operator_forced_takeover' }),
    onSuccess: async (handoff) => {
      setSelectedTicketId(handoff.ticket_id)
      setToast({ message: '已强制接管，未完成 AI 回复已被取消。', tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err: Error) => setToast({ message: err.message || '强制接管失败', tone: 'danger' }),
  })

  const releaseMutation = useMutation({
    mutationFn: (requestId: number) => api.webchatReleaseHandoff(requestId),
    onSuccess: async () => {
      setToast({ message: '会话已释放回待接入队列，AI 仍保持暂停。', tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err: Error) => setToast({ message: err.message || '释放失败', tone: 'danger' }),
  })

  const resumeAiMutation = useMutation({
    mutationFn: (requestId: number) => api.webchatResumeAi(requestId),
    onSuccess: async () => {
      setToast({ message: 'AI 已恢复，下一条客户消息可重新触发自动回复。', tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err: Error) => setToast({ message: err.message || '恢复 AI 失败', tone: 'danger' }),
  })

  const snippet = '<script src="https://YOUR_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" data-title="Speedaf Support" data-locale="en" async></script>'

  return (
    <AppShell>
      <PageHeader
        eyebrow="Webchat"
        title="网站聊天收件箱"
        description="客户侧结构化交互运行时：普通消息、Quick Reply、Handoff、Action 审计全部进入工单。WebChat ACK/card/handoff 均为 local-only，不代表 WhatsApp/Telegram/SMS/Email 外发。"
        actions={<Button variant="secondary" onClick={() => client.invalidateQueries({ queryKey: ['webchatConversations'] })}>刷新</Button>}
      />

      {allowDebug ? <Card className="soft">
        <CardHeader title="Speedaf Webchat 嵌入代码" subtitle="visitor 端无需登录；admin 后台需要登录。生产环境请替换为正式域名，并配置 WEBCHAT_ALLOWED_ORIGINS。" />
        <CardBody>
          <pre className="code-block"><code>{snippet}</code></pre>
          <div className="section-subtitle">Realtime-lite 使用 after_id events JSON long-poll；如事件接口不可用，仍保留 7s/10s polling fallback。连续失败时自动 backoff，成功后恢复。</div>
        </CardBody>
      </Card> : null}

      <div className="page-grid workspace">
        <Card>
          <CardHeader title="接管队列" subtitle="AI 递交、客户请求和 AI 正在对话都会进入这里处理。" />
          <CardBody>
            <div style={{ marginBottom: 12 }}>
              <SegmentedControl
                value={handoffView}
                onChange={(next) => setHandoffView(next as 'requested' | 'ai_active' | 'mine')}
                options={[
                  { value: 'requested', label: '待接入' },
                  { value: 'ai_active', label: 'AI 监控' },
                  { value: 'mine', label: '我的' },
                ]}
              />
            </div>
            {handoffQueue.isLoading ? <Skeleton lines={4} /> : null}
            <div className="list">
              {(handoffQueue.data?.items ?? []).map((item) => (
                <HandoffQueueItem
                  key={`${item.status}-${item.ticket_id}-${item.id ?? item.ai_turn_id ?? item.webchat_conversation_id}`}
                  item={item}
                  selected={selectedTicketId === item.ticket_id}
                  onSelect={() => setSelectedTicketId(item.ticket_id)}
                  onAccept={() => typeof item.id === 'number' && acceptMutation.mutate(item.id)}
                  onDecline={() => typeof item.id === 'number' && declineMutation.mutate(item.id)}
                  onForce={() => forceMutation.mutate(item.ticket_id)}
                  busy={acceptMutation.isPending || declineMutation.isPending || forceMutation.isPending}
                />
              ))}
              {!handoffQueue.isLoading && !(handoffQueue.data?.items?.length) ? <EmptyState text="当前队列为空。" /> : null}
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Webchat 会话" subtitle="按最近更新时间排序。needs human 表示客户请求人工或 AI/规则建议人工。" />
          <CardBody>
            {conversations.isLoading ? <Skeleton lines={8} /> : null}
            <div className="list">
              {(conversations.data ?? []).map((item) => (
                <button key={item.conversation_id} className={`queue-card ${selectedTicketId === item.ticket_id ? 'selected' : ''}`} onClick={() => setSelectedTicketId(item.ticket_id)}>
                  <div className="queue-card-top"><div className="badges">
                    <Badge tone={statusTone(item.status)}>{sanitizeDisplayText(item.status)}</Badge>
                    <Badge tone="success">WebChat</Badge>
                    <AIStatusBadge status={item.ai_status} pending={item.ai_pending} turnId={item.ai_turn_id} />
                    {item.ai_suspended ? <Badge tone="warning">AI paused</Badge> : null}
                    {item.handoff_status && item.handoff_status !== 'none' ? <Badge tone={handoffTone(item.handoff_status)}>{sanitizeDisplayText(item.handoff_status)}</Badge> : null}
                    {item.last_message_type ? <Badge>{sanitizeDisplayText(item.last_message_type)}</Badge> : null}
                    {item.needs_human ? <Badge tone="warning">Needs human</Badge> : null}
                    {incomingVoiceByTicket.get(item.ticket_id) ? <Badge tone="warning">Incoming WebCall</Badge> : null}
                  </div></div>
                  <div className="queue-card-title">{sanitizeDisplayText(item.ticket_no)} · {sanitizeDisplayText(item.title)}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor')}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.origin || 'unknown origin')} · {formatDateTime(item.updated_at)}</div>
                </button>
              ))}
              {!conversations.isLoading && !(conversations.data?.length) ? <EmptyState text="还没有 Webchat 会话。打开 /webchat/demo.html 或嵌入 widget 发送一条消息即可测试。" /> : null}
            </div>
          </CardBody>
        </Card>

        <div className="stack">
          <AgentWebCallPanel
            ticketId={selectedTicketId}
            ticketNo={selectedConversation?.ticket_no}
            conversationId={selectedConversation?.conversation_id}
            visitorLabel={selectedConversation?.visitor_name || selectedConversation?.visitor_email || selectedConversation?.visitor_phone || 'Anonymous visitor'}
            onSelectTicket={setSelectedTicketId}
            onActivity={() => {
              void client.invalidateQueries({ queryKey: ['webchatThread', selectedTicketId] })
              void client.invalidateQueries({ queryKey: ['webchatConversations'] })
              void client.invalidateQueries({ queryKey: ['webchatVoiceIncomingSessions'] })
            }}
          />

          <Card>
            <CardHeader title="会话详情" subtitle="展示访客来源、结构化卡片、客户 action、handoff 和完整消息。" />
            <CardBody>
              {thread.isLoading && selectedTicketId ? <Skeleton lines={8} /> : null}
              {selectedConversation ? (
                <div className="stack">
                  <div className="kv-grid">
                    <div className="kv"><label>工单</label><div>{sanitizeDisplayText(selectedConversation.ticket_no)}</div></div>
                    <div className="kv"><label>访客</label><div>{sanitizeDisplayText(selectedConversation.visitor_name || selectedConversation.visitor_email || selectedConversation.visitor_phone || 'Anonymous')}</div></div>
                    <div className="kv"><label>来源网站</label><div>{sanitizeDisplayText(selectedConversation.origin)}</div></div>
                    <div className="kv"><label>页面</label><div>{sanitizeDisplayText(selectedConversation.page_url)}</div></div>
                    <div className="kv"><label>当前状态</label><div>{sanitizeDisplayText(threadData?.conversation_state || selectedConversation.status)}</div></div>
                    <div className="kv"><label>接管状态</label><div><Badge tone={handoffTone(threadData?.handoff_status || selectedConversation.handoff_status)}>{sanitizeDisplayText(threadData?.handoff_status || selectedConversation.handoff_status || 'none')}</Badge></div></div>
                    {allowDebug ? <div className="kv"><label>AI Runtime</label><div><AIStatusBadge status={threadData?.ai_status || selectedConversation.ai_status} pending={threadData?.ai_pending || selectedConversation.ai_pending} turnId={threadData?.ai_turn_id || selectedConversation.ai_turn_id} /></div></div> : null}
                    {allowDebug ? <div className="kv"><label>Realtime-lite</label><div>{events.isFetching ? 'polling events…' : `after_id ${lastEventId}`}</div></div> : null}
                    <div className="kv"><label>Required action</label><div>{sanitizeDisplayText(threadData?.required_action || 'None')}</div></div>
                  </div>
                  {selectedHandoff ? <div className="message" data-role="agent">
                    <div className="message-head"><strong>接管控制</strong><span>{sanitizeDisplayText(selectedHandoff.source)} · {sanitizeDisplayText(selectedHandoff.trigger_type)}</span></div>
                    <div className="stack compact">
                      <div>{sanitizeDisplayText(selectedHandoff.reason_text || selectedHandoff.reason_code || 'Human handoff requested')}</div>
                      {selectedHandoff.recommended_agent_action ? <div className="section-subtitle">{sanitizeDisplayText(selectedHandoff.recommended_agent_action)}</div> : null}
                      <div className="badges">
                        {selectedHandoff.status === 'requested' && typeof selectedHandoff.id === 'number' ? <Button variant="primary" disabled={acceptMutation.isPending} onClick={() => acceptMutation.mutate(selectedHandoff.id as number)}>接管</Button> : null}
                        {selectedHandoff.status === 'accepted' && selectedHandoff.active_agent_id === session.data?.id && typeof selectedHandoff.id === 'number' ? <Button variant="secondary" disabled={releaseMutation.isPending} onClick={() => releaseMutation.mutate(selectedHandoff.id as number)}>释放回队列</Button> : null}
                        {typeof selectedHandoff.id === 'number' ? <Button variant="secondary" disabled={resumeAiMutation.isPending} onClick={() => resumeAiMutation.mutate(selectedHandoff.id as number)}>恢复 AI</Button> : null}
                      </div>
                    </div>
                  </div> : null}
                  {!selectedHandoff && selectedConversation?.ai_pending ? <div className="message" data-role="agent">
                    <div className="message-head"><strong>AI 正在处理</strong><span>{sanitizeDisplayText(selectedConversation.ai_status || 'active')}</span></div>
                    <Button variant="danger" disabled={forceMutation.isPending} onClick={() => selectedTicketId && forceMutation.mutate(selectedTicketId)}>强制接管</Button>
                  </div> : null}
                  <div className="timeline">
                    {(threadData?.messages ?? []).map((msg) => <MessageCard key={msg.id} msg={msg} allowDebug={allowDebug} />)}
                    {allowDebug && threadData?.actions?.length ? <div className="message" data-role="agent"><div className="message-head"><strong>Action audit</strong><span>{threadData.actions.length} actions</span></div><PayloadBlock payload={threadData.actions} allowDebug={allowDebug} /></div> : null}
                    {threadData && !(threadData.messages ?? []).length ? <EmptyState text="该会话暂无消息。" /> : null}
                  </div>
                </div>
              ) : <EmptyState text="请选择一个 Webchat 会话。" />}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="人工回复" subtitle="回复通道由 Outbound Channel Capability API 控制；WebChat 回复只写 local delivery，不进入真实外部 provider dispatch。" />
            <CardBody>
              <div className="stack">
                <div className="badges">
                  {outboundCapabilities.isLoading ? <Badge tone="warning">加载回复通道能力…</Badge> : null}
                  {outboundCapabilities.isError ? <Badge tone="danger">Capability API unavailable</Badge> : null}
                  {webchatReplyChannel ? <Badge tone={webchatReplyEnabled ? 'success' : 'danger'}>WebChat {sanitizeDisplayText(webchatReplyChannel.status)}</Badge> : null}
                  {visibleReplyChannels.map((channel) => <Badge key={channel.channel} tone={channel.channel === 'web_chat' ? 'success' : 'warning'}>{sanitizeDisplayText(channel.label)}</Badge>)}
                </div>
                {!webchatReplyEnabled ? <div className="section-subtitle">WebChat 回复当前未开放：{sanitizeDisplayText(webchatCapabilityIssue)}</div> : null}
                {handoffReplyBlocked ? <div className="section-subtitle">该会话需要先由当前客服接管后才能回复。</div> : null}
                <Field label="回复内容"><Textarea value={reply} onChange={(event) => setReply(event.target.value)} placeholder="例如：We have received your request and will check it shortly." /></Field>
                <label className="check-row"><input type="checkbox" checked={hasFactEvidence} onChange={(event) => setHasFactEvidence(event.target.checked)} /><span>本次回复涉及物流事实时，我已核对系统证据</span></label>
                <label className="check-row"><input type="checkbox" checked={confirmReview} onChange={(event) => setConfirmReview(event.target.checked)} /><span>若安全门返回 review，我确认已人工复核并继续发送</span></label>
                <Button variant="primary" disabled={!selectedTicketId || !reply.trim() || !webchatReplyEnabled || handoffReplyBlocked || replyMutation.isPending} onClick={() => replyMutation.mutate()}>{replyMutation.isPending ? '发送中…' : '发送 Webchat 回复'}</Button>
              </div>
            </CardBody>
          </Card>
        </div>
      </div>
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebchatInboxPage,
})
