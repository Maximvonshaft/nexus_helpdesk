import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { WebchatConversation } from '@/lib/types'
import { webchatVoiceApi } from '@/lib/webchatVoiceApi'
import type { WebchatVoiceIncomingSession } from '@/lib/webchatVoiceTypes'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { AgentWebCallPanel } from '@/components/webcall/AgentWebCallPanel'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { RequireCapability } from '@/components/security/RequireCapability'
import { routeAccess } from '@/lib/rbac'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'

function valueOrDash(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '-'
  return sanitizeDisplayText(String(value))
}

function visitorLabel(item?: WebchatConversation | null) {
  if (!item) return 'Anonymous visitor'
  return item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor'
}

function incomingVisitorLabel(item?: WebchatVoiceIncomingSession | null) {
  if (!item) return 'Anonymous visitor'
  return item.visitor_label || 'Anonymous visitor'
}

function WebCallWorkbenchPage() {
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh(true)
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)

  const conversations = useQuery({
    queryKey: ['webchatConversations'],
    queryFn: ({ signal }) => api.webchatConversations({ signal }),
    refetchInterval: autoRefresh.enabled ? 10000 : false,
    retry: false,
  })

  const incomingSessions = useQuery({
    queryKey: ['webchatVoiceIncomingSessions'],
    queryFn: ({ signal }) => webchatVoiceApi.incomingSessions({ status: 'ringing', limit: 50 }, { signal }),
    refetchInterval: autoRefresh.enabled ? 4000 : false,
    retry: false,
  })

  useEffect(() => {
    if (!selectedTicketId && incomingSessions.data?.items?.length) setSelectedTicketId(incomingSessions.data.items[0].ticket_id)
    else if (!selectedTicketId && conversations.data?.length) setSelectedTicketId(conversations.data[0].ticket_id)
  }, [conversations.data, incomingSessions.data, selectedTicketId])

  const selectedConversation = useMemo(
    () => (conversations.data ?? []).find((item) => item.ticket_id === selectedTicketId),
    [conversations.data, selectedTicketId],
  )
  const selectedIncomingSession = useMemo(
    () => (incomingSessions.data?.items ?? []).find((item) => item.ticket_id === selectedTicketId),
    [incomingSessions.data?.items, selectedTicketId],
  )

  async function refreshAll() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['webchatConversations'] }),
      client.invalidateQueries({ queryKey: ['webchatVoiceIncomingSessions'] }),
      client.invalidateQueries({ queryKey: ['webchatVoiceRuntimeConfig'] }),
      selectedTicketId ? client.invalidateQueries({ queryKey: ['webchatVoiceSessions', selectedTicketId] }) : Promise.resolve(),
      selectedTicketId ? client.invalidateQueries({ queryKey: ['webchatThread', selectedTicketId] }) : Promise.resolve(),
    ])
  }

  const incomingCount = incomingSessions.data?.items?.length ?? 0
  const liveConversationCount = conversations.data?.length ?? 0
  const selectedVisitor = selectedConversation ? visitorLabel(selectedConversation) : incomingVisitorLabel(selectedIncomingSession)

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/webcall']}>
        <PageHeader
          eyebrow="WebCall"
          title="WebCall 语音接听台"
          description="按模板工作台形态落地为真实队列：来电、客户上下文、接听/拒接/静音/挂断、通话证据和运行状态在同一页闭环。"
          actions={
            <div className="button-row">
              <Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>
                {autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}
              </Button>
              <Button onClick={() => void refreshAll()} disabled={conversations.isFetching || incomingSessions.isFetching}>
                {conversations.isFetching || incomingSessions.isFetching ? '刷新中...' : '立即刷新'}
              </Button>
            </div>
          }
        />

        <div className="metrics-grid">
          <MetricCard label="来电等待" value={incomingCount} hint="ringing WebCall sessions" />
          <MetricCard label="可关联会话" value={liveConversationCount} hint="WebChat tickets with voice context" />
          <MetricCard label="当前工单" value={selectedTicketId ?? '-'} hint="WebCall actions bind to ticket" />
          <MetricCard label="刷新状态" value={autoRefresh.enabled ? 'On' : 'Paused'} hint="queue refresh interval guarded" />
        </div>

        <div className="page-grid workspace">
          <div className="stack">
            <Card>
              <CardHeader title="来电队列" subtitle="优先展示正在响铃的 WebCall；没有来电时展示可关联 WebChat 会话。" />
              <CardBody>
                <div className="stack">
                  {incomingSessions.isLoading ? <div className="section-subtitle">正在加载来电队列...</div> : null}
                  {incomingSessions.isError ? <div className="message" data-role="agent">无法加载 WebCall 来电队列。</div> : null}
                  {(incomingSessions.data?.items ?? []).map((item) => (
                    <button
                      key={item.voice_session_id}
                      type="button"
                      className={`queue-card ${selectedTicketId === item.ticket_id ? 'selected' : ''}`}
                      onClick={() => setSelectedTicketId(item.ticket_id)}
                    >
                      <div className="badges">
                        <Badge tone="warning">Ringing</Badge>
                        <Badge>{valueOrDash(item.ticket_no)}</Badge>
                      </div>
                      <div className="queue-card-title">{valueOrDash(incomingVisitorLabel(item))}</div>
                      <div className="queue-card-meta">{valueOrDash(item.ticket_title)} · {valueOrDash(item.voice_session_id)}</div>
                      <div className="queue-card-meta">Ringing {valueOrDash(formatDateTime(item.ringing_at || undefined))}</div>
                    </button>
                  ))}
                  {!incomingSessions.isLoading && !incomingSessions.isError && incomingCount === 0 ? (
                    <EmptyState title="暂无响铃来电" description="没有 ringing WebCall 时，可从下方 WebChat 会话选择工单查看通话历史和操作面板。" />
                  ) : null}
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="WebChat 会话" subtitle="语音通话仍以 WebChat ticket 为证据归属，避免生成孤立通话记录。" />
              <CardBody>
                <div className="stack">
                  {conversations.isLoading ? <div className="section-subtitle">正在加载会话...</div> : null}
                  {conversations.isError ? <div className="message" data-role="agent">无法加载 WebChat 会话。</div> : null}
                  {(conversations.data ?? []).map((item) => (
                    <button
                      key={item.conversation_id}
                      type="button"
                      className={`queue-card ${selectedTicketId === item.ticket_id ? 'selected' : ''}`}
                      onClick={() => setSelectedTicketId(item.ticket_id)}
                    >
                      <div className="badges">
                        <Badge>{valueOrDash(item.ticket_no)}</Badge>
                        {item.needs_human ? <Badge tone="warning">需人工</Badge> : null}
                      </div>
                      <div className="queue-card-title">{valueOrDash(item.title)}</div>
                      <div className="queue-card-meta">{valueOrDash(visitorLabel(item))} · {valueOrDash(item.origin)}</div>
                    </button>
                  ))}
                  {!conversations.isLoading && !conversations.isError && !(conversations.data ?? []).length ? (
                    <EmptyState title="没有可关联会话" description="当前账号暂未看到可用于 WebCall 接听的 WebChat 会话。" />
                  ) : null}
                </div>
              </CardBody>
            </Card>
          </div>

          <Card>
            <CardHeader title="客户与工单上下文" subtitle="接电话前先确认客户、来源、页面和 ticket 归属。" />
            <CardBody>
              {selectedConversation ? (
                <div className="kv-grid">
                  <div className="kv"><label>Ticket</label><div>{valueOrDash(selectedConversation.ticket_no)}</div></div>
                  <div className="kv"><label>Ticket ID</label><div>{selectedConversation.ticket_id}</div></div>
                  <div className="kv"><label>访客</label><div>{valueOrDash(selectedVisitor)}</div></div>
                  <div className="kv"><label>Conversation</label><div>{valueOrDash(selectedConversation.conversation_id)}</div></div>
                  <div className="kv"><label>Origin</label><div>{valueOrDash(selectedConversation.origin)}</div></div>
                  <div className="kv"><label>Page</label><div>{valueOrDash(selectedConversation.page_url)}</div></div>
                </div>
              ) : selectedIncomingSession ? (
                <div className="kv-grid">
                  <div className="kv"><label>Ticket</label><div>{valueOrDash(selectedIncomingSession.ticket_no)}</div></div>
                  <div className="kv"><label>Ticket ID</label><div>{selectedIncomingSession.ticket_id}</div></div>
                  <div className="kv"><label>访客</label><div>{valueOrDash(selectedVisitor)}</div></div>
                  <div className="kv"><label>Conversation</label><div>{valueOrDash(selectedIncomingSession.conversation_id)}</div></div>
                  <div className="kv"><label>Origin</label><div>{valueOrDash(selectedIncomingSession.origin)}</div></div>
                  <div className="kv"><label>Page</label><div>{valueOrDash(selectedIncomingSession.page_url)}</div></div>
                </div>
              ) : (
                <EmptyState title="请选择一条 WebCall 或 WebChat 会话" description="选择后才能查看通话证据、运行状态和接听动作。" />
              )}
            </CardBody>
          </Card>

          <AgentWebCallPanel
            ticketId={selectedTicketId}
            ticketNo={selectedConversation?.ticket_no || selectedIncomingSession?.ticket_no}
            conversationId={selectedConversation?.conversation_id || selectedIncomingSession?.conversation_id}
            visitorLabel={selectedVisitor}
            onSelectTicket={setSelectedTicketId}
            onActivity={() => void refreshAll()}
          />
        </div>
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebCallWorkbenchPage,
})
