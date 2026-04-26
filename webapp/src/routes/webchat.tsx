import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText, statusTone } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Textarea } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'

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
    refetchInterval: 5000,
  })

  const selectedConversation = useMemo(
    () => (conversations.data ?? []).find((item) => item.ticket_id === selectedTicketId),
    [conversations.data, selectedTicketId],
  )

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
      setToast({ message: 'Webchat 回复已发送，访客端可见', tone: 'success' })
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

  const snippet = '<script src="https://YOUR_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" data-title="Speedaf Support" data-subtitle="Usually replies instantly" data-assistant-name="Speedy" async></script>'

  return (
    <AppShell>
      <PageHeader
        eyebrow="Webchat"
        title="网站聊天收件箱"
        description="把客户网站 Widget 的访客消息接入工单，并由客服在后台完成人工回复；所有回复都会经过安全门。"
        actions={
          <div className="button-row">
            <Button variant="secondary" onClick={() => client.invalidateQueries({ queryKey: ['webchatConversations'] })}>刷新</Button>
          </div>
        }
      />

      <Card className="soft">
        <CardHeader title="Speedaf Webchat 嵌入代码" subtitle="把这段代码放到客户网站页面底部即可。生产环境请替换为正式域名。" />
        <CardBody>
          <pre className="code-block"><code>{snippet}</code></pre>
          <div className="section-subtitle">访客只看到 Speedaf Support 标准客服聊天入口。</div>
        </CardBody>
      </Card>

      <div className="page-grid workspace">
        <Card>
          <CardHeader title="Webchat 会话" subtitle="按最近更新时间排序。点击会话后在右侧查看消息和回复。" />
          <CardBody>
            {conversations.isLoading ? <Skeleton lines={8} /> : null}
            <div className="list">
              {(conversations.data ?? []).map((item) => (
                <button
                  key={item.conversation_id}
                  className={`queue-card ${selectedTicketId === item.ticket_id ? 'selected' : ''}`}
                  onClick={() => setSelectedTicketId(item.ticket_id)}
                >
                  <div className="queue-card-top">
                    <div className="badges">
                      <Badge tone={statusTone(item.status)}>{sanitizeDisplayText(item.status)}</Badge>
                      <Badge tone="success">Webchat</Badge>
                    </div>
                  </div>
                  <div className="queue-card-title">{sanitizeDisplayText(item.ticket_no)} · {sanitizeDisplayText(item.title)}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor')}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.origin || 'unknown origin')} · {formatDateTime(item.updated_at)}</div>
                </button>
              ))}
              {!conversations.isLoading && !(conversations.data?.length) ? <EmptyState text="还没有 Webchat 会话。打开 /webchat/demo.html 发送一条消息即可测试。" /> : null}
            </div>
          </CardBody>
        </Card>

        <div className="stack">
          <Card>
            <CardHeader title="会话详情" subtitle="展示访客来源、页面 URL 和完整消息。" />
            <CardBody>
              {thread.isLoading && selectedTicketId ? <Skeleton lines={8} /> : null}
              {selectedConversation ? (
                <div className="stack">
                  <div className="kv-grid">
                    <div className="kv"><label>工单</label><div>{sanitizeDisplayText(selectedConversation.ticket_no)}</div></div>
                    <div className="kv"><label>访客</label><div>{sanitizeDisplayText(selectedConversation.visitor_name || selectedConversation.visitor_email || selectedConversation.visitor_phone || 'Anonymous')}</div></div>
                    <div className="kv"><label>来源网站</label><div>{sanitizeDisplayText(selectedConversation.origin)}</div></div>
                    <div className="kv"><label>页面</label><div>{sanitizeDisplayText(selectedConversation.page_url)}</div></div>
                  </div>
                  <div className="timeline">
                    {(thread.data?.messages ?? []).map((msg) => (
                      <div key={msg.id} className="message" data-role={msg.direction === 'visitor' ? 'user' : 'agent'}>
                        <div className="message-head">
                          <strong>{msg.direction === 'visitor' ? '访客' : '客服'}</strong>
                          <span>{formatDateTime(msg.created_at)}</span>
                        </div>
                        <div>{sanitizeDisplayText(msg.body)}</div>
                      </div>
                    ))}
                    {thread.data && !thread.data.messages.length ? <EmptyState text="该会话暂无消息。" /> : null}
                  </div>
                </div>
              ) : (
                <EmptyState text="请选择一个 Webchat 会话。" />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="人工回复" subtitle="回复前后端会执行 outbound safety gate；敏感内容会被阻断，物流事实承诺需证据或人工确认。" />
            <CardBody>
              <div className="stack">
                <Field label="回复内容">
                  <Textarea value={reply} onChange={(event) => setReply(event.target.value)} placeholder="例如：We have received your request and will check it shortly." />
                </Field>
                <label className="check-row">
                  <input type="checkbox" checked={hasFactEvidence} onChange={(event) => setHasFactEvidence(event.target.checked)} />
                  <span>本次回复涉及物流事实时，我已核对系统证据</span>
                </label>
                <label className="check-row">
                  <input type="checkbox" checked={confirmReview} onChange={(event) => setConfirmReview(event.target.checked)} />
                  <span>若安全门返回 review，我确认已人工复核并继续发送</span>
                </label>
                <Button variant="primary" disabled={!selectedTicketId || !reply.trim() || replyMutation.isPending} onClick={() => replyMutation.mutate()}>
                  {replyMutation.isPending ? '发送中…' : '发送 Webchat 回复'}
                </Button>
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
