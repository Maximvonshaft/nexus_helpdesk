import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText, statusTone } from '@/lib/format'
import type { WebchatCardAction, WebchatCardPayload, WebchatMessage } from '@/lib/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Textarea } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'

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

function MessageCard({ msg }: { msg: WebchatMessage }) {
  const messageType = msg.message_type || 'text'
  const cardPayload = isCardPayload(msg.payload_json) ? msg.payload_json : null
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
          <PayloadBlock payload={msg.payload_json} />
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
        <PayloadBlock payload={msg.payload_json} />
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

function WebchatInboxPage() {
  const client = useQueryClient()
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [reply, setReply] = useState('')
  const [hasFactEvidence, setHasFactEvidence] = useState(false)
  const [confirmReview, setConfirmReview] = useState(false)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const conversations = useQuery({
    queryKey: ['webchatConversations'],
    queryFn: api.webchatConversations,
    refetchInterval: 10000,
  })

  useEffect(() => {
    if (!selectedTicketId && conversations.data?.length) {
      setSelectedTicketId(conversations.data[0].ticket_id)
    }
  }, [conversations.data, selectedTicketId])

  const thread = useQuery({
    queryKey: ['webchatThread', selectedTicketId],
    queryFn: () => api.webchatThread(selectedTicketId as number),
    enabled: !!selectedTicketId,
    refetchInterval: 7000,
  })

  const selectedConversation = useMemo(
    () => (conversations.data ?? []).find((item) => item.ticket_id === selectedTicketId),
    [conversations.data, selectedTicketId],
  )
  const threadData = thread.data

  const replyMutation = useMutation({
    mutationFn: async () => {
      if (!selectedTicketId) return
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
    },
    onError: (err: Error) => {
      setToast({ message: err.message || '发送失败，已被安全门拦截或需要复核', tone: 'danger' })
    },
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

      <Card className="soft">
        <CardHeader title="Speedaf Webchat 嵌入代码" subtitle="visitor 端无需登录；admin 后台需要登录。生产环境请替换为正式域名，并配置 WEBCHAT_ALLOWED_ORIGINS。" />
        <CardBody>
          <pre className="code-block"><code>{snippet}</code></pre>
          <div className="section-subtitle">可选属性：data-tenant、data-channel、data-title、data-subtitle、data-assistant-name、data-locale、data-welcome、data-api-base。不会暴露内部 token。</div>
        </CardBody>
      </Card>

      <div className="page-grid workspace">
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
                    {item.last_message_type ? <Badge>{sanitizeDisplayText(item.last_message_type)}</Badge> : null}
                    {item.needs_human ? <Badge tone="warning">Needs human</Badge> : null}
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
                    <div className="kv"><label>AI Runtime</label><div><AIStatusBadge status={threadData?.ai_status || selectedConversation.ai_status} pending={threadData?.ai_pending || selectedConversation.ai_pending} turnId={threadData?.ai_turn_id || selectedConversation.ai_turn_id} /></div></div>
                    <div className="kv"><label>AI pending for</label><div>{sanitizeDisplayText(String(threadData?.ai_pending_for_message_id || selectedConversation.ai_pending_for_message_id || 'None'))}</div></div>
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

          <Card>
            <CardHeader title="人工回复" subtitle="回复会执行 outbound safety gate；WebChat 回复只写 local delivery，不进入真实外部 provider dispatch。" />
            <CardBody>
              <div className="stack">
                <Field label="回复内容"><Textarea value={reply} onChange={(event) => setReply(event.target.value)} placeholder="例如：We have received your request and will check it shortly." /></Field>
                <label className="check-row"><input type="checkbox" checked={hasFactEvidence} onChange={(event) => setHasFactEvidence(event.target.checked)} /><span>本次回复涉及物流事实时，我已核对系统证据</span></label>
                <label className="check-row"><input type="checkbox" checked={confirmReview} onChange={(event) => setConfirmReview(event.target.checked)} /><span>若安全门返回 review，我确认已人工复核并继续发送</span></label>
                <Button variant="primary" disabled={!selectedTicketId || !reply.trim() || replyMutation.isPending} onClick={() => replyMutation.mutate()}>{replyMutation.isPending ? '发送中…' : '发送 Webchat 回复'}</Button>
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
