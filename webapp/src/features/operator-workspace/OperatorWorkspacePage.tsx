import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, supportApi } from '@/lib/supportApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import type {
  UnifiedOperatorQueueItem,
  WorkspaceFilters,
  WorkspaceMobileView,
  WorkspaceScope,
} from '@/lib/operatorWorkspaceTypes'
import {
  evidencePresentation,
  messageDeliveryPresentation,
  outcomePresentation,
  ownerPresentation,
  priorityPresentation,
  queueSourcePresentation,
  retryPresentation,
  slaPresentation,
  sourceStatusPresentation,
} from '@/lib/operatorWorkspacePresentation'
import type {
  BadgeTone,
  SupportMemoryLedger,
  WebchatMessage,
  WebchatThread,
} from '@/lib/types'
import type { SpeedafCancelPreviewResponse } from '@/lib/speedafTypes'
import { useSession } from '@/hooks/useAuth'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'

type SpeedafActionKind = 'none' | 'waybill_lookup' | 'work_order' | 'address_update' | 'cancel'
type ActionResultEnvelope = { kind: SpeedafActionKind; result: Record<string, unknown> }
type CancelPreviewBinding = {
  fingerprint: string
  result: SpeedafCancelPreviewResponse
}

const defaultFilters: WorkspaceFilters = {
  state: 'active',
  sourceType: 'all',
  owner: 'any',
  priority: 'all',
  sla: 'any',
  retry: 'any',
  sort: 'oldest',
}

const mobileViews: Array<{ value: WorkspaceMobileView; label: string }> = [
  { value: 'queue', label: '队列' },
  { value: 'case', label: '案例' },
  { value: 'conversation', label: '沟通' },
  { value: 'actions', label: '动作' },
]

function initialQueueId() {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('queue')
}

function initialSessionKey() {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('session')
}

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function hasCapability(capabilities: Set<string>, ...values: string[]) {
  return values.some((value) => capabilities.has(value))
}

function safeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

function textValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function directionLabel(direction: string) {
  if (direction === 'visitor' || direction === 'customer') return '客户'
  if (direction === 'agent' || direction === 'human') return '客服'
  if (direction === 'ai') return 'AI'
  return '系统'
}

function isOutboundMessage(message: WebchatMessage) {
  return message.direction === 'agent' || message.direction === 'ai'
}

function supportMemoryFromThread(thread?: WebchatThread | null) {
  return thread?.support_memory ?? null
}

function cancelFingerprint(ticketId: number | null, waybill: string, caller: string, reasonCode: string) {
  return JSON.stringify({
    ticketId,
    waybill: waybill.trim().toUpperCase(),
    caller: caller.trim(),
    reasonCode: reasonCode.trim(),
  })
}

function PresentationBadge({ presentation }: { presentation: { label: string; detail?: string; tone: BadgeTone } }) {
  return (
    <span className="operator-presentation">
      <Badge tone={presentation.tone}>{presentation.label}</Badge>
      {presentation.detail ? <small>{presentation.detail}</small> : null}
    </span>
  )
}

function QueueFilters({ filters, onChange }: { filters: WorkspaceFilters; onChange: (filters: WorkspaceFilters) => void }) {
  return (
    <div className="operator-filter-grid" aria-label="队列筛选">
      <Field label="状态">
        <Select value={filters.state} onChange={(event) => onChange({ ...filters, state: event.target.value as WorkspaceFilters['state'] })}>
          <option value="active">需要处理</option>
          <option value="terminal">来源终态</option>
          <option value="all">全部</option>
        </Select>
      </Field>
      <Field label="来源">
        <Select value={filters.sourceType} onChange={(event) => onChange({ ...filters, sourceType: event.target.value as WorkspaceFilters['sourceType'] })}>
          <option value="all">全部来源</option>
          <option value="handoff">人工接管</option>
          <option value="ticket">客服工单</option>
          <option value="dispatch">运营派发</option>
        </Select>
      </Field>
      <Field label="责任人">
        <Select value={filters.owner} onChange={(event) => onChange({ ...filters, owner: event.target.value as WorkspaceFilters['owner'] })}>
          <option value="any">全部责任人</option>
          <option value="mine">我的</option>
          <option value="unassigned">未分配</option>
          <option value="team">我的团队</option>
        </Select>
      </Field>
      <Field label="SLA">
        <Select value={filters.sla} onChange={(event) => onChange({ ...filters, sla: event.target.value as WorkspaceFilters['sla'] })}>
          <option value="any">全部 SLA</option>
          <option value="breached">已超时</option>
          <option value="at_risk">即将超时</option>
          <option value="stale">长期未更新</option>
          <option value="paused">已暂停</option>
          <option value="healthy">正常</option>
          <option value="unavailable">不可用</option>
        </Select>
      </Field>
      <Field label="排序">
        <Select value={filters.sort} onChange={(event) => onChange({ ...filters, sort: event.target.value as WorkspaceFilters['sort'] })}>
          <option value="oldest">最早待办优先</option>
          <option value="newest">最新更新优先</option>
        </Select>
      </Field>
    </div>
  )
}

function QueueRow({ item, active, currentUserId, onSelect }: {
  item: UnifiedOperatorQueueItem
  active: boolean
  currentUserId?: number
  onSelect: () => void
}) {
  const source = queueSourcePresentation(item.source_type)
  const priority = priorityPresentation(item.priority)
  const owner = ownerPresentation(item.owner, currentUserId)
  const sla = slaPresentation(item.sla)
  const retry = retryPresentation(item.retry)
  const sourceStatus = sourceStatusPresentation(item.source_status)
  return (
    <button type="button" className={`operator-queue-row${active ? ' is-active' : ''}`} aria-pressed={active} onClick={onSelect}>
      <span className="operator-queue-row__top">
        <strong>{item.case_key || item.queue_id}</strong>
        <Badge tone={priority.tone}>{priority.label}</Badge>
      </span>
      <span className="operator-queue-row__meta">
        <Badge tone={source.tone}>{source.label}</Badge>
        {item.reopened ? <Badge tone="warning">已重新打开</Badge> : null}
        <span>{item.country_code} · {item.channel_key}</span>
      </span>
      <span className="operator-queue-row__status"><span>{owner.label}</span><span>{sla.label}</span></span>
      {item.source_type === 'dispatch' ? <span className="operator-queue-row__detail">{retry.label}</span> : null}
      <span className="operator-queue-row__detail">{sourceStatus.label}</span>
      <time>{formatDateTime(item.updated_at)}</time>
    </button>
  )
}

function QueueRail({ items, selectedQueueId, currentUserId, isLoading, isRefreshing, hasNextPage, isFetchingNextPage, onSelect, onLoadMore }: {
  items: UnifiedOperatorQueueItem[]
  selectedQueueId: string | null
  currentUserId?: number
  isLoading: boolean
  isRefreshing: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  onSelect: (item: UnifiedOperatorQueueItem) => void
  onLoadMore: () => void
}) {
  return (
    <section className="operator-queue-list" aria-label="统一操作队列" aria-busy={isLoading}>
      <div className="operator-section-head compact"><div><h2>统一队列</h2><p>人工接管、客服工单和运营派发使用同一任务入口。</p></div>{isRefreshing ? <Badge>刷新中</Badge> : null}</div>
      {isLoading ? <EmptyState title="正在读取队列" description="正在读取当前授权范围内的任务。" /> : null}
      {!isLoading && !items.length ? <EmptyState title="当前没有待处理任务" description="可以调整筛选条件或稍后刷新。" /> : null}
      <div className="operator-queue-items">
        {items.map((item) => (
          <QueueRow key={item.queue_id} item={item} active={item.queue_id === selectedQueueId} currentUserId={currentUserId} onSelect={() => onSelect(item)} />
        ))}
      </div>
      {hasNextPage ? <Button size="sm" loading={isFetchingNextPage} onClick={onLoadMore}>加载更多任务</Button> : null}
    </section>
  )
}

function CaseHeader({ item, currentUserId }: { item: UnifiedOperatorQueueItem; currentUserId?: number }) {
  const source = queueSourcePresentation(item.source_type)
  const status = sourceStatusPresentation(item.source_status)
  const owner = ownerPresentation(item.owner, currentUserId)
  const sla = slaPresentation(item.sla)
  const retry = retryPresentation(item.retry)
  return (
    <header className="operator-case-header">
      <div><div className="operator-case-kicker">{source.label} · {item.country_code} · {item.channel_key}</div><h1>{item.case_key || item.queue_id}</h1><p>来源记录 {item.source_type}:{item.source_id}{item.ticket_id ? ` · Ticket #${item.ticket_id}` : ''}</p></div>
      <div className="operator-case-statuses" aria-label="案例状态">
        <PresentationBadge presentation={status} />
        <PresentationBadge presentation={owner} />
        <PresentationBadge presentation={sla} />
        {item.source_type === 'dispatch' ? <PresentationBadge presentation={retry} /> : null}
      </div>
    </header>
  )
}

function EvidencePanel({ memory }: { memory: SupportMemoryLedger | null }) {
  const timeline = memory?.evidence_timeline ?? []
  return (
    <section className="operator-evidence-panel" aria-labelledby="operator-evidence-title">
      <div className="operator-section-head compact"><div><h2 id="operator-evidence-title">事实与证据</h2><p>客户主张、知识、AI 建议、人工决定和运营结果明确分开。</p></div></div>
      {!timeline.length ? <EmptyState title="暂无结构化证据" description="可继续查看来源摘要和会话，但不要把缺失证据当成事实。" /> : null}
      <div className="operator-evidence-list">
        {timeline.map((item, index) => {
          const presentation = evidencePresentation(item)
          return (
            <article key={`${item.kind}-${item.source_id || index}`} className={presentation.className}>
              <header><Badge tone={presentation.tone}>{presentation.label}</Badge>{item.created_at ? <time>{formatDateTime(item.created_at)}</time> : null}</header>
              <strong>{sanitizeDisplayText(item.label || item.kind)}</strong>
              {presentation.detail ? <p>{presentation.detail}</p> : null}
              {item.summary && Object.keys(item.summary).length ? <TechnicalDetails title="证据摘要" summary="默认收起"><pre>{JSON.stringify(item.summary, null, 2)}</pre></TechnicalDetails> : null}
            </article>
          )
        })}
      </div>
    </section>
  )
}

function replyMutationSafety(error: unknown) {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') return null
  const detail = error.detail as { safety?: { reasons?: string[] } }
  return detail.safety ? { reasons: detail.safety.reasons ?? [] } : null
}

function ConversationPanel({ item, thread, isLoading, isRefreshing, error, capabilities, onRefresh, onReplyDirtyChange, selectionUnavailable }: {
  item: UnifiedOperatorQueueItem
  thread: WebchatThread | null
  isLoading: boolean
  isRefreshing: boolean
  error: unknown
  capabilities: Set<string>
  onRefresh: () => Promise<void>
  onReplyDirtyChange: (dirty: boolean) => void
  selectionUnavailable: boolean
}) {
  const [reply, setReply] = useState('')
  const [confirmReview, setConfirmReview] = useState(false)
  const [isNearMessageBottom, setIsNearMessageBottom] = useState(true)
  const [newMessageCount, setNewMessageCount] = useState(0)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const previousMessageCountRef = useRef(thread?.messages.length ?? 0)
  const canReply = Boolean(item.ticket_id && thread && !selectionUnavailable && hasCapability(capabilities, 'outbound.send', 'webchat.handoff.accept'))

  useEffect(() => {
    setReply('')
    setConfirmReview(false)
    setNewMessageCount(0)
    previousMessageCountRef.current = 0
  }, [item.queue_id])

  useLayoutEffect(() => {
    const currentCount = thread?.messages.length ?? 0
    const added = Math.max(0, currentCount - previousMessageCountRef.current)
    previousMessageCountRef.current = currentCount
    if (!added) return
    const list = messagesRef.current
    if (list && isNearMessageBottom) {
      list.scrollTo({ top: list.scrollHeight, behavior: 'smooth' })
      setNewMessageCount(0)
    } else {
      setNewMessageCount((count) => count + added)
    }
  }, [isNearMessageBottom, thread?.messages.length])

  useEffect(() => onReplyDirtyChange(Boolean(reply.trim())), [onReplyDirtyChange, reply])
  useEffect(() => () => onReplyDirtyChange(false), [onReplyDirtyChange])

  const replyMutation = useMutation({
    mutationFn: () => operatorWorkspaceApi.reply(item.ticket_id as number, reply.trim(), confirmReview),
    onSuccess: async () => {
      setReply('')
      setConfirmReview(false)
      onReplyDirtyChange(false)
      await onRefresh()
    },
    onError: (mutationError) => {
      if (replyMutationSafety(mutationError)) setConfirmReview(true)
    },
  })
  const mutationSafety = replyMutationSafety(replyMutation.error)

  return (
    <section id="workspace-conversation" className="operator-conversation-panel" aria-labelledby="operator-conversation-title" tabIndex={-1}>
      <div className="operator-section-head compact"><div><h2 id="operator-conversation-title">客户沟通</h2><p>回复始终经过服务端权限、事实和安全检查。</p></div>{isRefreshing ? <Badge>刷新中</Badge> : null}</div>
      {isLoading ? <EmptyState title="正在读取会话" description="正在载入客户消息。" /> : null}
      {error ? <ErrorSummary title="会话暂不可用" errors={[errorCopy(error, '仍可基于案例摘要继续分诊')]} /> : null}
      {thread ? (
        <>
          <div
            ref={messagesRef}
            className="operator-message-list"
            aria-live="polite"
            onScroll={(event) => {
              const target = event.currentTarget
              const nearBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 80
              setIsNearMessageBottom(nearBottom)
              if (nearBottom) setNewMessageCount(0)
            }}
          >
            {thread.messages.map((message) => {
              const delivery = messageDeliveryPresentation(message.delivery_status)
              return (
                <article key={message.id} className={`operator-message is-${message.direction}`}>
                  <header><strong>{sanitizeDisplayText(message.author_label || directionLabel(message.direction))}</strong>{message.created_at ? <time>{formatDateTime(message.created_at)}</time> : null}</header>
                  <p>{sanitizeDisplayText(message.body_text || message.body)}</p>
                  {isOutboundMessage(message) ? <footer aria-label="送达状态"><Badge tone={delivery.tone}>{delivery.label}</Badge>{delivery.detail ? <span>{delivery.detail}</span> : null}</footer> : null}
                </article>
              )
            })}
            {!thread.messages.length ? <EmptyState title="暂无消息" description="该会话尚无可显示内容。" /> : null}
          </div>
          {newMessageCount ? <Button size="sm" variant="secondary" onClick={() => { messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: 'smooth' }); setNewMessageCount(0) }}>{newMessageCount} 条新消息</Button> : null}
          {mutationSafety ? <ErrorSummary title="回复需要人工复核" errors={mutationSafety.reasons.length ? mutationSafety.reasons : ['请检查内容后再次确认发送']} /> : null}
          {replyMutation.isError && !mutationSafety ? <ErrorSummary title="发送失败" errors={[errorCopy(replyMutation.error, '请稍后重试')]} /> : null}
          <form className="operator-reply" onSubmit={(event) => { event.preventDefault(); if (canReply && reply.trim()) replyMutation.mutate() }}>
            <Field label="回复客户" description="技术发送成功不自动等于客户收到或案例结案。" disabledReason={!canReply ? '当前权限、会话或队列状态不允许回复' : undefined}>
              <Textarea value={reply} onChange={(event) => { setReply(event.target.value); setConfirmReview(false) }} rows={4} placeholder="输入清晰、可验证的客户回复…" autoComplete="off" />
            </Field>
            <Button type="submit" variant="primary" loading={replyMutation.isPending} loadingLabel="发送中…" disabled={!canReply || !reply.trim()}>{confirmReview ? '确认发送' : '发送回复'}</Button>
          </form>
        </>
      ) : !isLoading ? <EmptyState title="当前案例没有可用会话" description="可以继续查看案例证据，回复和人工接管暂不可用。" /> : null}
    </section>
  )
}

function actionDisabledReason({ action, item, capabilities, waybill, caller, description, whatsappPhone }: {
  action: SpeedafActionKind
  item: UnifiedOperatorQueueItem
  capabilities: Set<string>
  waybill: string
  caller: string
  description: string
  whatsappPhone: string
}) {
  if (action === 'none') return '请先选择一个与当前任务有关的动作'
  if (!item.ticket_id) return '当前案例没有可执行动作的 Ticket'
  if (action === 'waybill_lookup') return caller.trim() ? '' : '缺少客户电话'
  if (!waybill.trim()) return '缺少运单'
  if (!caller.trim()) return '缺少客户电话'
  if (action === 'work_order' && !hasCapability(capabilities, 'tool:speedaf.work_order.create:write')) return '当前权限不允许创建催派工单'
  if (action === 'address_update' && !hasCapability(capabilities, 'tool:speedaf.order.update_address:write')) return '当前权限不允许更新联系号码'
  if (action === 'cancel' && !hasCapability(capabilities, 'tool:speedaf.order.cancel:write')) return '当前权限不允许请求取消'
  if (action === 'work_order' && !description.trim()) return '缺少催派说明'
  if (action === 'address_update' && !whatsappPhone.trim()) return '缺少确认后的联系号码'
  return ''
}

function ActionPanel({ item, thread, capabilities, onRefresh }: {
  item: UnifiedOperatorQueueItem
  thread: WebchatThread | null
  capabilities: Set<string>
  onRefresh: () => Promise<void>
}) {
  const [action, setAction] = useState<SpeedafActionKind>('none')
  const [waybill, setWaybill] = useState('')
  const [caller, setCaller] = useState('')
  const [countryCode, setCountryCode] = useState(item.country_code || 'CH')
  const [description, setDescription] = useState('')
  const [whatsappPhone, setWhatsappPhone] = useState('')
  const [reasonCode, setReasonCode] = useState('CC01')
  const [cancelPreview, setCancelPreview] = useState<CancelPreviewBinding | null>(null)

  useEffect(() => {
    setAction('none')
    setWaybill('')
    setCaller(thread?.visitor?.phone || '')
    setWhatsappPhone(thread?.visitor?.phone || '')
    setCountryCode(item.country_code || 'CH')
    setDescription('')
    setReasonCode('CC01')
    setCancelPreview(null)
  }, [item.queue_id, item.country_code, thread?.visitor?.phone])

  const invalidateCancelPreview = () => setCancelPreview(null)
  const currentCancelFingerprint = cancelFingerprint(item.ticket_id, waybill, caller, reasonCode)

  const handoffMutation = useMutation({
    mutationFn: async (kind: 'accept' | 'force' | 'release' | 'resume' | 'decline') => {
      const handoff = thread?.handoff
      if (kind === 'accept' && handoff?.id) return supportApi.webchatAcceptHandoff(handoff.id, 'Accepted from Operator Workspace')
      if (kind === 'force' && item.ticket_id) return supportApi.webchatForceTakeover(item.ticket_id, { reason_code: 'operator_takeover', note: 'Operator Workspace takeover' })
      if (kind === 'release' && handoff?.id) return supportApi.webchatReleaseHandoff(handoff.id, 'Released from Operator Workspace')
      if (kind === 'resume' && handoff?.id) return supportApi.webchatResumeAi(handoff.id, 'Resume AI from Operator Workspace')
      if (kind === 'decline' && handoff?.id) return operatorWorkspaceApi.declineHandoff(handoff.id, 'operator_capacity', 'Declined from Operator Workspace')
      throw new Error('当前接管动作不可执行')
    },
    onSuccess: onRefresh,
  })

  const actionMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      if (action === 'waybill_lookup') {
        const result = await supportApi.querySpeedafWaybills(item.ticket_id, { callerID: caller.trim(), countryCode: countryCode.trim().toUpperCase() })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      if (action === 'work_order') {
        const result = await supportApi.createSpeedafWorkOrder(item.ticket_id, { waybillCode: waybill.trim().toUpperCase(), callerID: caller.trim(), workOrderType: 'WT0103-05', description: description.trim() })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      if (action === 'address_update') {
        const result = await supportApi.submitSpeedafAddressUpdate(item.ticket_id, { waybillCode: waybill.trim().toUpperCase(), callerID: caller.trim(), whatsAppPhone: whatsappPhone.trim() })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      throw new Error('请先选择可执行动作')
    },
    onSuccess: onRefresh,
  })

  const cancelPreviewMutation = useMutation({
    mutationFn: async () => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      const fingerprint = currentCancelFingerprint
      const result = await supportApi.previewSpeedafCancel(item.ticket_id, { waybillCode: waybill.trim().toUpperCase(), callerID: caller.trim(), reasonCode })
      return { fingerprint, result }
    },
    onSuccess: setCancelPreview,
  })

  const cancelConfirmMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      if (!cancelPreview || cancelPreview.fingerprint !== currentCancelFingerprint) throw new Error('取消预检已失效，请基于当前运单、电话和原因重新预检')
      if (!cancelPreview.result.cancelAllowed || !cancelPreview.result.confirmToken) throw new Error('当前预检不允许提交取消请求')
      const result = await supportApi.confirmSpeedafCancel(item.ticket_id, {
        waybillCode: waybill.trim().toUpperCase(),
        callerID: caller.trim(),
        reasonCode,
        confirmToken: cancelPreview.result.confirmToken,
      })
      return { kind: 'cancel', result: result as unknown as Record<string, unknown> }
    },
    onSuccess: async () => { setCancelPreview(null); await onRefresh() },
  })

  const disabledReason = actionDisabledReason({ action, item, capabilities, waybill, caller, description, whatsappPhone })
  const busy = handoffMutation.isPending || actionMutation.isPending || cancelPreviewMutation.isPending || cancelConfirmMutation.isPending
  const actionError = handoffMutation.error || actionMutation.error || cancelPreviewMutation.error || cancelConfirmMutation.error
  const envelope = actionMutation.data || cancelConfirmMutation.data
  const resultRecord = envelope?.result ?? {}
  const resultPresentation = envelope ? outcomePresentation(resultRecord.status, resultRecord.message) : null
  const candidates = Array.isArray(resultRecord.candidates) ? resultRecord.candidates.map(safeRecord) : []
  const handoff = thread?.handoff
  const handoffAllowed = hasCapability(capabilities, 'webchat.handoff.accept', 'webchat.handoff.force_takeover', 'webchat.handoff.release', 'webchat.handoff.resume_ai')

  return (
    <section id="workspace-actions" className="operator-actions-panel" aria-labelledby="operator-actions-title" tabIndex={-1}>
      <div className="operator-section-head compact"><div><h2 id="operator-actions-title">下一步动作</h2><p>不可执行原因直接说明，服务端拥有最终授权。</p></div><Badge tone="warning">服务端最终授权</Badge></div>
      <div className="operator-action-group">
        <h3>案例接管</h3>
        <div className="operator-button-row">
          {handoff?.can_accept || handoff?.can_force_takeover ? <Button variant="primary" loading={handoffMutation.isPending} disabled={!handoffAllowed} onClick={() => handoffMutation.mutate(handoff?.can_accept ? 'accept' : 'force')}>接管案例</Button> : null}
          {handoff?.can_decline ? <Button onClick={() => handoffMutation.mutate('decline')}>暂不接管</Button> : null}
          {handoff?.can_release ? <Button variant="ghost" onClick={() => handoffMutation.mutate('release')}>释放案例</Button> : null}
          {handoff?.can_resume_ai ? <Button variant="ghost" onClick={() => handoffMutation.mutate('resume')}>恢复 AI</Button> : null}
        </div>
        {handoff?.reason_text ? <p><strong>接管原因：</strong>{sanitizeDisplayText(handoff.reason_text)}</p> : null}
      </div>

      <div className="operator-action-group">
        <h3>Speedaf 受控动作</h3>
        <Field label="选择动作"><Select value={action} onChange={(event) => { setAction(event.target.value as SpeedafActionKind); invalidateCancelPreview(); actionMutation.reset(); cancelConfirmMutation.reset() }}><option value="none">请选择动作</option><option value="waybill_lookup">电话查单（只读）</option><option value="work_order">创建催派工单</option><option value="address_update">提交联系号码更新</option><option value="cancel">取消预检与确认</option></Select></Field>
        {action !== 'none' ? (
          <>
            <div className="operator-form-grid">
              {action !== 'waybill_lookup' ? <Field label="运单" required><Input value={waybill} onChange={(event) => { setWaybill(event.target.value.toUpperCase()); invalidateCancelPreview() }} autoComplete="off" /></Field> : null}
              <Field label="客户电话" required><Input type="tel" value={caller} onChange={(event) => { setCaller(event.target.value); invalidateCancelPreview() }} autoComplete="off" /></Field>
            </div>
            {action === 'waybill_lookup' ? <Field label="国家代码" required><Input value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} /></Field> : null}
            {action === 'work_order' ? <Field label="催派说明" required><Textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></Field> : null}
            {action === 'address_update' ? <Field label="确认后的联系号码" required><Input type="tel" value={whatsappPhone} onChange={(event) => setWhatsappPhone(event.target.value)} /></Field> : null}
            {action === 'cancel' ? <Field label="取消原因" required><Select value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); invalidateCancelPreview() }}><option value="CC01">派送太慢</option><option value="CC02">快递员服务问题</option><option value="CC03">不支持验货</option><option value="CC04">不支持部分签收</option><option value="CC05">其他原因</option></Select></Field> : null}
          </>
        ) : null}
        {disabledReason ? <p className="operator-disabled-reason"><strong>不可执行原因：</strong>{disabledReason}</p> : null}
        {actionError ? <ErrorSummary title="动作未完成" errors={[errorCopy(actionError, '请稍后重试')]} /> : null}
        {candidates.length ? <div className="operator-candidates"><strong>候选运单</strong>{candidates.map((candidate) => <div key={textValue(candidate.waybillCode)}><span>{sanitizeDisplayText(textValue(candidate.waybillCode))}</span><Button size="sm" onClick={() => { setWaybill(textValue(candidate.waybillCode)); setAction('work_order'); invalidateCancelPreview() }}>填入催派</Button></div>)}</div> : null}
        {cancelPreview ? <div className={`operator-action-receipt ${cancelPreview.result.cancelAllowed ? 'is-neutral' : 'is-warning'}`} role="status"><strong>{cancelPreview.result.cancelAllowed ? '预检允许提交取消请求' : '当前状态不允许取消'}</strong><p>{sanitizeDisplayText(cancelPreview.result.currentStatusLabel || cancelPreview.result.reasonLabel || '未返回原因')}</p><small>预检不是取消完成；预检绑定当前 Ticket、运单、电话和原因，任一输入变化后必须重新预检。</small></div> : null}
        {resultPresentation ? <div className={`operator-action-receipt is-${resultPresentation.tone}`} role="status"><strong>{resultPresentation.label}</strong><p>{resultPresentation.detail}</p>{numberValue(resultRecord.jobId) ? <TechnicalDetails title="请求追踪" summary="技术标识"><code>Job #{numberValue(resultRecord.jobId)}</code></TechnicalDetails> : null}</div> : null}
        <div className="operator-button-row">
          {action === 'cancel' ? <><Button loading={cancelPreviewMutation.isPending} disabled={Boolean(disabledReason) || busy} onClick={() => cancelPreviewMutation.mutate()}>先做取消预检</Button><Button variant="danger" loading={cancelConfirmMutation.isPending} disabled={!cancelPreview?.result.cancelAllowed || !cancelPreview.result.confirmToken || cancelPreview.fingerprint !== currentCancelFingerprint || busy} onClick={() => cancelConfirmMutation.mutate()}>确认提交取消请求</Button></> : action !== 'none' ? <Button variant={action === 'work_order' ? 'primary' : 'secondary'} loading={actionMutation.isPending} disabled={Boolean(disabledReason) || busy} onClick={() => actionMutation.mutate()}>{action === 'waybill_lookup' ? '查询运单' : action === 'work_order' ? '创建催派工单' : '提交联系号码更新'}</Button> : null}
        </div>
      </div>
    </section>
  )
}

export function OperatorWorkspacePage({ scope }: { scope: WorkspaceScope }) {
  const queryClient = useQueryClient()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const [filters, setFilters] = useState<WorkspaceFilters>(defaultFilters)
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(() => initialQueueId())
  const [requestedSessionKey] = useState<string | null>(() => initialSessionKey())
  const [mobileView, setMobileView] = useState<WorkspaceMobileView>('queue')
  const [replyDraftDirty, setReplyDraftDirty] = useState(false)
  const [replyDiscardOpen, setReplyDiscardOpen] = useState(false)
  const pendingReplyActionRef = useRef<(() => void) | null>(null)
  const [retainedSelectedItem, setRetainedSelectedItem] = useState<UnifiedOperatorQueueItem | null>(null)

  useEffect(() => { document.title = '操作员工作台 · Nexus OSR' }, [])
  useEffect(() => {
    if (!replyDraftDirty) return undefined
    const protectDraft = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', protectDraft)
    return () => window.removeEventListener('beforeunload', protectDraft)
  }, [replyDraftDirty])
  useLayoutEffect(() => {
    const targetId: Record<WorkspaceMobileView, string> = {
      queue: 'workspace-queue',
      case: 'workspace-case',
      conversation: 'workspace-conversation',
      actions: 'workspace-actions',
    }
    document.getElementById(targetId[mobileView])?.focus({ preventScroll: true })
  }, [mobileView])
  useEffect(() => {
    const url = new URL(window.location.href)
    if (selectedQueueId) {
      url.searchParams.set('queue', selectedQueueId)
      url.searchParams.delete('session')
    } else {
      url.searchParams.delete('queue')
      if (!requestedSessionKey) url.searchParams.delete('session')
    }
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }, [requestedSessionKey, selectedQueueId])

  const canReadQueue = hasCapability(capabilities, 'operator_queue.read')
  const requestedConversation = useQuery({
    queryKey: ['operatorWorkspaceSessionDeepLink', scope, requestedSessionKey],
    queryFn: () => supportApi.supportConversationDetail(requestedSessionKey || ''),
    enabled: Boolean(session.data && canReadQueue && requestedSessionKey),
    retry: false,
  })
  const requestedQueueId = useMemo(() => {
    const conversation = requestedConversation.data?.conversation
    if (conversation?.handoff_request_id) return `handoff:${conversation.handoff_request_id}`
    if (conversation?.ticket_id) return `ticket:${conversation.ticket_id}`
    return null
  }, [requestedConversation.data?.conversation])
  const queue = useInfiniteQuery({
    queryKey: ['operatorWorkspaceQueue', scope, filters],
    queryFn: ({ pageParam }) => operatorWorkspaceApi.unifiedQueue(scope, filters, pageParam as string | null),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
    enabled: Boolean(session.data && canReadQueue),
    retry: false,
    refetchInterval: 15_000,
  })
  const queueItems = useMemo(() => queue.data?.pages.flatMap((page) => page.items) ?? [], [queue.data?.pages])
  const selectedQueueItem = useMemo(() => queueItems.find((item) => item.queue_id === selectedQueueId) ?? null, [queueItems, selectedQueueId])
  const requestedQueueItem = useMemo(() => queueItems.find((item) => item.queue_id === requestedQueueId) ?? null, [queueItems, requestedQueueId])
  const resolvingSessionDeepLink = Boolean(
    requestedSessionKey
    && !requestedConversation.isError
    && (
      requestedConversation.isLoading
      || (requestedQueueId && !requestedQueueItem && (queue.isLoading || queue.hasNextPage || queue.isFetchingNextPage))
    ),
  )
  const selectedQueueItemMissing = Boolean(selectedQueueId && !selectedQueueItem && retainedSelectedItem?.queue_id === selectedQueueId)
  const preserveMissingSelection = replyDraftDirty && selectedQueueItemMissing
  const selectedItem = selectedQueueItem
    ?? (preserveMissingSelection ? retainedSelectedItem : null)
    ?? requestedQueueItem
    ?? (resolvingSessionDeepLink ? null : queueItems[0] ?? null)

  useEffect(() => { if (selectedQueueItem) setRetainedSelectedItem(selectedQueueItem) }, [selectedQueueItem])
  useEffect(() => {
    if (requestedQueueItem && selectedQueueId !== requestedQueueItem.queue_id) setSelectedQueueId(requestedQueueItem.queue_id)
    else if (!selectedQueueId && selectedItem && !resolvingSessionDeepLink) setSelectedQueueId(selectedItem.queue_id)
    else if (selectedQueueId && !selectedQueueItem && !replyDraftDirty) setSelectedQueueId(queueItems[0]?.queue_id ?? null)
  }, [queueItems, replyDraftDirty, requestedQueueItem, resolvingSessionDeepLink, selectedItem, selectedQueueId, selectedQueueItem])
  useEffect(() => {
    if (requestedQueueId && !requestedQueueItem && queue.hasNextPage && !queue.isFetchingNextPage) {
      void queue.fetchNextPage()
    }
  }, [queue, requestedQueueId, requestedQueueItem])

  const thread = useQuery({
    queryKey: ['operatorWorkspaceThread', selectedItem?.queue_id, selectedItem?.source_links.conversation],
    queryFn: () => operatorWorkspaceApi.conversationThread(selectedItem?.source_links.conversation || ''),
    enabled: Boolean(selectedItem?.source_links.conversation),
    retry: false,
    refetchInterval: selectedItem?.source_links.conversation ? 5_000 : false,
  })
  const sourceRecord = useQuery({
    queryKey: ['operatorWorkspaceSourceRecord', selectedItem?.queue_id, selectedItem?.source_links.ticket],
    queryFn: () => operatorWorkspaceApi.sourceRecord(selectedItem?.source_links.ticket || ''),
    enabled: Boolean(selectedItem?.source_links.ticket && !selectedItem?.source_links.conversation),
    retry: false,
  })
  const refreshSelected = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceQueue'] }),
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceThread', selectedItem?.queue_id] }),
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceSourceRecord', selectedItem?.queue_id] }),
    ])
  }
  const runWithReplyDraftGuard = (action: () => void) => {
    if (!replyDraftDirty) return action()
    pendingReplyActionRef.current = action
    setReplyDiscardOpen(true)
  }
  const selectItem = (item: UnifiedOperatorQueueItem) => runWithReplyDraftGuard(() => { setSelectedQueueId(item.queue_id); setMobileView('case') })
  const memory = supportMemoryFromThread(thread.data)

  return (
    <main className={`operator-workspace is-mobile-${mobileView}`} data-testid="operator-workspace">
      <div className="operator-mobile-nav" role="navigation" aria-label="移动端工作区">
        {mobileViews.map((view) => <button key={view.value} type="button" className={mobileView === view.value ? 'is-active' : ''} aria-pressed={mobileView === view.value} onClick={() => setMobileView(view.value)}>{view.label}</button>)}
      </div>
      {session.isError ? <ErrorSummary title="无法读取当前用户" errors={[errorCopy(session.error, '请重新登录')]} /> : null}
      {session.data && !canReadQueue ? <EmptyState title="当前权限不允许访问操作队列" description="需要 operator_queue.read；前端不会绕过服务端授权。" /> : null}
      {session.data && canReadQueue ? (
        <div className="operator-layout">
          <aside id="workspace-queue" className="operator-queue-pane" tabIndex={-1}>
            <QueueFilters filters={filters} onChange={(next) => runWithReplyDraftGuard(() => { setFilters(next); setSelectedQueueId(null) })} />
            {queue.isError ? <ErrorSummary title="统一队列不可用" errors={[errorCopy(queue.error, '请检查当前授权范围')]} action={<Button onClick={() => queue.refetch()}>重新加载</Button>} /> : null}
            <QueueRail items={queueItems} selectedQueueId={selectedItem?.queue_id ?? null} currentUserId={session.data.id} isLoading={queue.isLoading} isRefreshing={queue.isFetching && !queue.isLoading} hasNextPage={Boolean(queue.hasNextPage)} isFetchingNextPage={queue.isFetchingNextPage} onSelect={selectItem} onLoadMore={() => queue.fetchNextPage()} />
          </aside>
          <section id="workspace-case" className="operator-case-pane" aria-label="当前案例" tabIndex={-1}>
            {selectedItem ? <><CaseHeader item={selectedItem} currentUserId={session.data.id} />{preserveMissingSelection ? <div className="operator-selection-stale" role="status"><strong>当前任务已离开队列，回复草稿仍保留</strong><p>发送和受控动作已暂停；切换任务前需要确认是否放弃草稿。</p></div> : null}{sourceRecord.data && !thread.data ? <section className="operator-source-summary"><h2>来源记录摘要</h2><dl><div><dt>标题</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.data.title) || '未提供')}</dd></div><div><dt>状态</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.data.status) || selectedItem.source_status)}</dd></div><div><dt>优先级</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.data.priority) || selectedItem.priority)}</dd></div></dl></section> : null}<EvidencePanel memory={memory} /><ConversationPanel item={selectedItem} thread={thread.data ?? null} isLoading={thread.isLoading} isRefreshing={thread.isFetching && !thread.isLoading} error={thread.error} capabilities={capabilities} onRefresh={refreshSelected} onReplyDirtyChange={setReplyDraftDirty} selectionUnavailable={preserveMissingSelection} /></> : <EmptyState title="选择一个任务开始处理" description="从统一队列选择人工接管、客服工单或运营派发任务。" />}
          </section>
          <aside className="operator-context-pane" aria-label="案例动作与结果">
            {selectedItem ? <><section className="operator-current-task"><h2>当前任务</h2><strong>{sanitizeDisplayText(memory?.required_action || memory?.next_actions?.[0]?.label || '核实当前事实并决定下一步')}</strong><small>前端建议不替代服务端权限、政策和结果权威。</small></section>{preserveMissingSelection ? <EmptyState title="当前任务动作已暂停" description="该任务已不在授权队列中。" /> : <ActionPanel item={selectedItem} thread={thread.data ?? null} capabilities={capabilities} onRefresh={refreshSelected} />}</> : <EmptyState title="暂无动作" description="选择案例后显示允许动作和结果。" />}
          </aside>
        </div>
      ) : null}
      <ConfirmDialog open={replyDiscardOpen} title="放弃未发送的回复？" description="切换案例或筛选后，这段回复不会被保留。" confirmLabel="放弃回复" destructive onOpenChange={(open) => { setReplyDiscardOpen(open); if (!open) pendingReplyActionRef.current = null }} onConfirm={() => { const action = pendingReplyActionRef.current; pendingReplyActionRef.current = null; setReplyDiscardOpen(false); setReplyDraftDirty(false); action?.() }} />
    </main>
  )
}
