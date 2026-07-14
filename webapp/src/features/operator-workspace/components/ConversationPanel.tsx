import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Textarea } from '@/components/ui/Field'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { messageDeliveryPresentation } from '@/lib/operatorWorkspacePresentation'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import { ApiError } from '@/lib/supportApi'
import type { UnifiedOperatorQueueItem } from '@/lib/operatorWorkspaceTypes'
import type { WebchatThread } from '@/lib/types'
import { errorCopy, hasCapability, isOutboundMessage, messageAuthorLabel, reducedMotionPreferred } from '../workspaceUtils'

const MESSAGE_BOTTOM_THRESHOLD = 96

function isNearMessageBottom(node: HTMLElement) {
  return node.scrollHeight - node.scrollTop - node.clientHeight <= MESSAGE_BOTTOM_THRESHOLD
}

function replyReview(error: unknown) {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') return null
  const detail = error.detail as { safety?: { reasons?: string[] } }
  return detail.safety ? { reasons: detail.safety.reasons ?? [] } : null
}

export function ConversationPanel({
  item,
  thread,
  loading,
  error,
  capabilities,
  selectionUnavailable,
  onRefresh,
  onDirtyChange,
}: {
  item: UnifiedOperatorQueueItem
  thread: WebchatThread | null
  loading: boolean
  error: unknown
  capabilities: Set<string>
  selectionUnavailable: boolean
  onRefresh: () => Promise<void>
  onDirtyChange: (dirty: boolean) => void
}) {
  const [reply, setReply] = useState('')
  const [confirmReview, setConfirmReview] = useState(false)
  const [newMessageCount, setNewMessageCount] = useState(0)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const followsLatestRef = useRef(true)
  const lastQueueIdRef = useRef<string | null>(null)
  const lastMessageCountRef = useRef(0)

  const canReply = Boolean(
    !selectionUnavailable
    && thread
    && item.ticket_id
    && hasCapability(capabilities, 'outbound.send')
    && thread.handoff?.can_reply !== false,
  )

  const replyMutation = useMutation({
    mutationFn: () => {
      if (!item.ticket_id) throw new Error('当前案例没有可回复的工单')
      return operatorWorkspaceApi.reply(item.ticket_id, reply.trim(), confirmReview)
    },
    onSuccess: async () => {
      setReply('')
      setConfirmReview(false)
      await onRefresh()
    },
    onError: (mutationError) => {
      if (replyReview(mutationError)) setConfirmReview(true)
    },
  })
  const resetReplyMutationRef = useRef(replyMutation.reset)

  useEffect(() => {
    resetReplyMutationRef.current = replyMutation.reset
  }, [replyMutation.reset])

  useEffect(() => {
    const dirty = Boolean(reply.trim())
    onDirtyChange(dirty)
    if (!dirty) return
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warnBeforeUnload)
    return () => window.removeEventListener('beforeunload', warnBeforeUnload)
  }, [onDirtyChange, reply])

  useEffect(() => {
    setReply('')
    setConfirmReview(false)
    setNewMessageCount(0)
    followsLatestRef.current = true
    resetReplyMutationRef.current()
  }, [item.queue_id])

  const scrollToLatest = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const node = messagesRef.current
    if (!node) return
    node.scrollTo({
      top: node.scrollHeight,
      behavior: reducedMotionPreferred() ? 'auto' : behavior,
    })
    followsLatestRef.current = true
    setNewMessageCount(0)
  }, [])

  useLayoutEffect(() => {
    const count = thread?.messages.length ?? 0
    const queueChanged = lastQueueIdRef.current !== item.queue_id
    const added = Math.max(0, count - lastMessageCountRef.current)
    lastQueueIdRef.current = item.queue_id
    lastMessageCountRef.current = count

    if (queueChanged) {
      followsLatestRef.current = true
      setNewMessageCount(0)
      window.requestAnimationFrame(() => scrollToLatest('auto'))
      return
    }
    if (!added) return
    if (followsLatestRef.current) window.requestAnimationFrame(() => scrollToLatest('smooth'))
    else setNewMessageCount((current) => current + added)
  }, [item.queue_id, scrollToLatest, thread?.messages.length])

  const review = replyReview(replyMutation.error)
  const disabledReason = selectionUnavailable
    ? '当前任务已不在你的待办范围内'
    : !thread
      ? '当前案例没有可回复的客户会话'
      : !item.ticket_id
        ? '当前案例没有可回复的工单'
        : !hasCapability(capabilities, 'outbound.send')
          ? '当前账号没有发送客户回复的权限'
          : thread.handoff?.can_reply === false
            ? '请先接管案例后再回复客户'
            : ''

  return (
    <section className="conversation-panel" aria-labelledby="conversation-title">
      <div className="workspace-section-heading">
        <div>
          <h2 id="conversation-title">客户沟通</h2>
          <p>只发送清晰、可验证、能帮助客户理解下一步的回复。</p>
        </div>
        {thread?.unread_count ? <Badge tone="warning">{thread.unread_count} 条未读</Badge> : null}
      </div>

      {loading ? <EmptyState title="正在读取沟通记录" description="请稍候。" /> : null}
      {error ? <ErrorSummary title="沟通记录暂不可用" errors={[errorCopy(error, '请稍后重新加载')]} /> : null}
      {!loading && !error && !thread ? <EmptyState title="没有客户会话" description="该案例仍可根据工单和运营记录继续处理。" /> : null}

      {thread ? (
        <>
          <div
            className="conversation-messages"
            ref={messagesRef}
            onScroll={(event) => {
              followsLatestRef.current = isNearMessageBottom(event.currentTarget)
              if (followsLatestRef.current) setNewMessageCount(0)
            }}
          >
            {thread.messages.length ? thread.messages.map((message) => {
              const delivery = isOutboundMessage(message) ? messageDeliveryPresentation(message.delivery_status) : null
              return (
                <article className={`conversation-message is-${message.direction === 'visitor' ? 'customer' : 'service'}`} key={message.id}>
                  <header>
                    <strong>{messageAuthorLabel(message)}</strong>
                    {message.created_at ? <time>{formatDateTime(message.created_at)}</time> : null}
                  </header>
                  <p>{sanitizeDisplayText(message.body_text || message.body)}</p>
                  {delivery ? <small className={`delivery-state is-${delivery.tone}`}>{delivery.label}{delivery.detail ? ` · ${delivery.detail}` : ''}</small> : null}
                </article>
              )
            }) : <EmptyState title="还没有消息" description="等待客户消息或使用下方输入框主动回复。" />}
          </div>

          {newMessageCount ? (
            <Button variant="secondary" onClick={() => scrollToLatest()}>{newMessageCount} 条新消息，查看最新</Button>
          ) : null}

          {replyMutation.error ? (
            <ErrorSummary
              title={review ? '回复需要再次确认' : '回复发送失败'}
              errors={review?.reasons?.length ? review.reasons : [errorCopy(replyMutation.error, '请检查内容后重试')]}
            />
          ) : null}

          <form
            className="conversation-composer"
            onSubmit={(event) => {
              event.preventDefault()
              if (canReply && reply.trim()) replyMutation.mutate()
            }}
          >
            <Field
              label="回复客户"
              description="先说明已核实的事实，再说明下一步和预计结果。"
              disabledReason={disabledReason || undefined}
            >
              <Textarea
                name="operator-reply-body"
                value={reply}
                onChange={(event) => {
                  setReply(event.target.value)
                  setConfirmReview(false)
                }}
                rows={4}
                placeholder="输入给客户的回复…"
                autoComplete="off"
              />
            </Field>
            <Button
              type="submit"
              variant="primary"
              loading={replyMutation.isPending}
              loadingLabel="发送中…"
              disabled={!canReply || !reply.trim()}
            >
              {confirmReview ? '确认发送' : '发送回复'}
            </Button>
          </form>
        </>
      ) : null}
    </section>
  )
}
