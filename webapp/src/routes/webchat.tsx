import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText, statusTone } from '@/lib/format'
import type { WebchatCardPayload, WebchatMessage } from '@/lib/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'

function isCardPayload(payload: WebchatMessage['payload_json']): payload is WebchatCardPayload {
  return Boolean(payload && typeof payload === 'object' && 'card_type' in payload && 'actions' in payload)
}

function PayloadBlock({ payload }: { payload: unknown }) {
  const [open, setOpen] = useState(false)
  if (!payload || typeof payload !== 'object') return null
  return (
    <div className="stack compact">
      <Button variant="secondary" onClick={() => setOpen((value) => !value)}>{open ? '收起 payload' : '查看 payload'}</Button>
      {open ? <pre className="code-block"><code>{sanitizeDisplayText(JSON.stringify(payload, null, 2))}</code></pre> : null}
    </div>
  )
}

function MessageCard({ msg }: { msg: WebchatMessage }) {
  const messageType = msg.message_type || 'text'
  const cardPayload = isCardPayload(msg.payload_json) ? msg.payload_json : null
  if (messageType === 'card') {
    return (
      <div className="message" data-role="agent">
        <div className="message-head"><strong>结构化卡片 · {sanitizeDisplayText(cardPayload?.card_type || 'card')}</strong><span>{formatDateTime(msg.created_at)}</span></div>
        <div className="stack compact">
          <div><strong>{sanitizeDisplayText(cardPayload?.title || msg.body_text || msg.body)}</strong></div>
          {cardPayload?.body ? <div>{sanitizeDisplayText(cardPayload.body)}</div> : null}
          <PayloadBlock payload={msg.payload_json} />
        </div>
      </div>
    )
  }
  if (messageType === 'action' || msg.direction === 'action') {
    return (
      <div className="message" data-role="user">
        <div className="message-head"><strong>客户动作</strong><span>{formatDateTime(msg.created_at)}</span></div>
        <div>{sanitizeDisplayText(msg.body_text || msg.body)}</div>
        <PayloadBlock payload={msg.payload_json} />
      </div>
    )
  }
  return (
    <div className="message" data-role={msg.direction === 'visitor' ? 'user' : 'agent'}>
      <div className="message-head"><strong>{msg.direction === 'visitor' ? '访客' : msg.direction === 'system' ? '系统' : msg.author_label ? sanitizeDisplayText(msg.author_label) : '客服 / AI'}</strong><span>{formatDateTime(msg.created_at)}</span></div>
      <div>{sanitizeDisplayText(msg.body_text || msg.body)}</div>
    </div>
  )
}

async function fetchWebchatEvents(ticketId: number, afterId: number, signal?: AbortSignal) {
  const token = getToken()
  const params = new URLSearchParams({ after_id: String(afterId), limit: '50', wait_ms: '1500' })
  const response = await fetch(`/api/webchat/admin/tickets/${ticketId}/events?${params.toString()}`, { headers: token ? { Authorization: `Bearer ${token}` } : {}, signal })
  if (!response.ok) throw new Error(`events_poll_failed:${response.status}`)
  return response.json() as Promise<{ events: { id: number; event_type: string }[]; last_event_id: number }>
}

function backoffMs(failures: number, baseMs: number, maxMs: number) {
  if (failures <= 0) return baseMs
  return Math.min(maxMs, baseMs * 2 ** Math.min(failures, 4))
}

function WebchatInboxPage() {
  const client = useQueryClient()
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [lastEventId, setLastEventId] = useState(0)
  const [eventPollFailures, setEventPollFailures] = useState(0)
  const [conversationPollFailures, setConversationPollFailures] = useState(0)

  const conversations = useQuery({ queryKey: ['webchatConversations'], queryFn: ({ signal }) => api.webchatConversations({ signal }), refetchInterval: backoffMs(conversationPollFailures, 10000, 60000), retry: false })

  useEffect(() => {
    if (conversations.isSuccess) setConversationPollFailures(0)
    if (conversations.isError) setConversationPollFailures((value) => Math.min(value + 1, 6))
  }, [conversations.isSuccess, conversations.isError, conversations.dataUpdatedAt, conversations.errorUpdatedAt])

  useEffect(() => {
    if (!selectedTicketId && conversations.data?.length) setSelectedTicketId(conversations.data[0].ticket_id)
  }, [conversations.data, selectedTicketId])

  useEffect(() => {
    setLastEventId(0)
    setEventPollFailures(0)
  }, [selectedTicketId])

  const thread = useQuery({ queryKey: ['webchatThread', selectedTicketId], queryFn: ({ signal }) => api.webchatThread(selectedTicketId as number, { signal }), enabled: !!selectedTicketId, refetchInterval: 7000, retry: false })
  const events = useQuery({ queryKey: ['webchatEvents', selectedTicketId, lastEventId], queryFn: ({ signal }) => fetchWebchatEvents(selectedTicketId as number, lastEventId, signal), enabled: !!selectedTicketId, refetchInterval: backoffMs(eventPollFailures, 2500, 30000), retry: false })

  useEffect(() => {
    if (events.isSuccess) setEventPollFailures(0)
    if (events.isError) setEventPollFailures((value) => Math.min(value + 1, 6))
  }, [events.isSuccess, events.isError, events.dataUpdatedAt, events.errorUpdatedAt])

  useEffect(() => {
    if (!selectedTicketId || !events.data?.events?.length) return
    setLastEventId(events.data.last_event_id || events.data.events[events.data.events.length - 1].id)
    void client.invalidateQueries({ queryKey: ['webchatThread', selectedTicketId] })
    void client.invalidateQueries({ queryKey: ['webchatConversations'] })
  }, [client, events.data, selectedTicketId])

  const selectedConversation = useMemo(() => (conversations.data ?? []).find((item) => item.ticket_id === selectedTicketId), [conversations.data, selectedTicketId])
  const threadData = thread.data
  const snippet = '<script src="https://YOUR_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" data-title="Speedaf Support" data-locale="en" async></script>'

  return (
    <AppShell>
      <PageHeader eyebrow="Webchat" title="网站聊天收件箱" description="Webchat 当前为 intake-only：只接收客户问题并创建/更新工单；客户回复必须从 Ticket workflow 通过 Email 或 WhatsApp 发送。" actions={<Button variant="secondary" onClick={() => client.invalidateQueries({ queryKey: ['webchatConversations'] })}>刷新</Button>} />

      <Card className="soft">
        <CardHeader title="Speedaf Webchat 嵌入代码" subtitle="Webchat 只作为入口。默认不再展示或发送 Webchat local reply。" />
        <CardBody>
          <pre className="code-block"><code>{snippet}</code></pre>
          <div className="section-subtitle">Outbound policy: Webchat inbound only. Follow-up must be sent by Email or WhatsApp from the Ticket workflow after human approval.</div>
        </CardBody>
      </Card>

      <Card className="soft">
        <CardHeader title="Webchat outbound disabled" subtitle="普通客服不再通过 Webchat 回复客户。后端 API 也会返回 409 webchat_outbound_disabled_intake_only。" />
      </Card>

      <div className="page-grid workspace">
        <Card>
          <CardHeader title="Webchat 会话" subtitle="按最近更新时间排序。Webchat 仅用于 intake 和审计。" />
          <CardBody>
            {conversations.isLoading ? <Skeleton lines={8} /> : null}
            <div className="list">
              {(conversations.data ?? []).map((item) => (
                <button key={item.conversation_id} className={`queue-card ${selectedTicketId === item.ticket_id ? 'selected' : ''}`} onClick={() => setSelectedTicketId(item.ticket_id)}>
                  <div className="queue-card-top"><div className="badges"><Badge tone={statusTone(item.status)}>{sanitizeDisplayText(item.status)}</Badge><Badge tone="success">WebChat Intake</Badge>{item.needs_human ? <Badge tone="warning">Needs human</Badge> : null}</div></div>
                  <div className="queue-card-title">{sanitizeDisplayText(item.ticket_no)} · {sanitizeDisplayText(item.title)}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor')}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.origin || 'unknown origin')} · {formatDateTime(item.updated_at)}</div>
                </button>
              ))}
              {!conversations.isLoading && !(conversations.data?.length) ? <EmptyState text="还没有 Webchat intake 会话。" /> : null}
            </div>
          </CardBody>
        </Card>

        <div className="stack">
          <Card>
            <CardHeader title="会话详情" subtitle="只读展示 Webchat inbound 消息、客户动作和审计内容。" />
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
                    <div className="kv"><label>Realtime-lite</label><div>{events.isFetching ? 'polling events…' : `after_id ${lastEventId}`}</div></div>
                    <div className="kv"><label>Required action</label><div>{sanitizeDisplayText(threadData?.required_action || 'None')}</div></div>
                  </div>
                  <div className="timeline">
                    {(threadData?.messages ?? []).map((msg) => <MessageCard key={msg.id} msg={msg} />)}
                    {threadData?.actions?.length ? <div className="message" data-role="agent"><div className="message-head"><strong>Action audit</strong><span>{threadData.actions.length} actions</span></div><PayloadBlock payload={threadData.actions} /></div> : null}
                    {threadData && !(threadData.messages ?? []).length ? <EmptyState text="该会话暂无消息。" /> : null}
                  </div>
                </div>
              ) : <EmptyState text="请选择一个 Webchat 会话。" />}
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  )
}

export const Route = createRoute({ getParentRoute: () => RootRoute, path: '/webchat', beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) }, component: WebchatInboxPage })
