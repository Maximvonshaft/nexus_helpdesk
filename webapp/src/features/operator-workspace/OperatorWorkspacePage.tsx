import { useEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { ApiError, supportApi } from '@/lib/supportApi'
import { operatorWorkspaceApi, loadWorkspaceScope, saveWorkspaceScope } from '@/lib/operatorWorkspaceApi'
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
  SupportMemoryTimelineItem,
  WebchatMessage,
  WebchatThread,
} from '@/lib/types'
import { useLogout, useSession } from '@/hooks/useAuth'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'

type SpeedafActionKind = 'none' | 'waybill_lookup' | 'work_order' | 'address_update' | 'cancel'
type ActionResultEnvelope = { kind: SpeedafActionKind; result: Record<string, unknown> }
type CancelPreview = {
  cancelAllowed: boolean
  confirmToken?: string | null
  currentStatusLabel?: string | null
  reasonLabel?: string | null
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

function latestCustomerClaim(thread?: WebchatThread | null) {
  return [...(thread?.messages ?? [])].reverse().find((message) => message.direction === 'visitor')
}

function timelineHas(memory: SupportMemoryLedger | null, values: string[]) {
  const normalizedValues = values.map((value) => value.toLowerCase())
  return (memory?.evidence_timeline ?? []).some((item) => {
    const haystack = `${item.status || ''} ${item.label || ''} ${JSON.stringify(item.summary || {})}`.toLowerCase()
    return normalizedValues.some((value) => haystack.includes(value))
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

function ScopeEditor({
  draft,
  onChange,
  onApply,
  applied,
}: {
  draft: WorkspaceScope
  onChange: (scope: WorkspaceScope) => void
  onApply: () => void
  applied: boolean
}) {
  const errors = [
    !draft.tenantKey.trim() ? '请输入部署方分配的 Tenant。系统不会猜测工作范围。' : '',
    draft.countryCode.trim().length < 2 ? '国家代码至少需要 2 个字符。' : '',
    !draft.channelKey.trim() ? '请输入渠道键。' : '',
  ].filter(Boolean)
  return (
    <section className="operator-scope" aria-labelledby="operator-scope-title">
      <div className="operator-section-head">
        <div>
          <h2 id="operator-scope-title">工作范围</h2>
          <p>队列只会读取后端授权的 Tenant、国家和渠道。范围错误时将拒绝加载。</p>
        </div>
        {applied ? <Badge tone="success">已应用</Badge> : <Badge tone="warning">待应用</Badge>}
      </div>
      <div className="operator-scope-grid">
        <Field label="Tenant" required description="使用管理员分配的工作域标识。">
          <Input
            name="workspace-tenant"
            value={draft.tenantKey}
            onChange={(event) => onChange({ ...draft, tenantKey: event.target.value })}
            autoComplete="organization"
            placeholder="例如：default"
          />
        </Field>
        <Field label="国家" required description="ISO 国家代码或后台配置的国家键。">
          <Input
            name="workspace-country"
            value={draft.countryCode}
            onChange={(event) => onChange({ ...draft, countryCode: event.target.value.toUpperCase() })}
            autoComplete="country"
            placeholder="CH"
          />
        </Field>
        <Field label="渠道" required description="必须与授权范围完全一致。">
          <Input
            name="workspace-channel"
            value={draft.channelKey}
            onChange={(event) => onChange({ ...draft, channelKey: event.target.value.toLowerCase() })}
            autoComplete="off"
            placeholder="webchat"
          />
        </Field>
      </div>
      {errors.length ? <ErrorSummary title="当前范围还不能使用" errors={errors} /> : null}
      <Button variant="primary" disabled={Boolean(errors.length)} onClick={onApply}>
        应用工作范围
      </Button>
    </section>
  )
}

function QueueFilters({
  filters,
  onChange,
}: {
  filters: WorkspaceFilters
  onChange: (filters: WorkspaceFilters) => void
}) {
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

function QueueRow({
  item,
  active,
  currentUserId,
  onSelect,
}: {
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
    <button
      type="button"
      className={`operator-queue-row${active ? ' is-active' : ''}`}
      aria-pressed={active}
      onClick={onSelect}
    >
      <span className="operator-queue-row__top">
        <strong>{item.case_key || item.queue_id}</strong>
        <Badge tone={priority.tone}>{priority.label}</Badge>
      </span>
      <span className="operator-queue-row__meta">
        <Badge tone={source.tone}>{source.label}</Badge>
        {item.reopened ? <Badge tone="warning">已重新打开</Badge> : null}
        <span>{item.country_code} · {item.channel_key}</span>
      </span>
      <span className="operator-queue-row__status">
        <span>{owner.label}</span>
        <span className={sla.tone === 'danger' ? 'is-danger' : sla.tone === 'warning' ? 'is-warning' : ''}>{sla.label}</span>
      </span>
      {item.source_type === 'dispatch' ? <span className="operator-queue-row__detail">{retry.label}</span> : null}
      <span className="operator-queue-row__detail">{sourceStatus.label}</span>
      <time>{formatDateTime(item.updated_at)}</time>
    </button>
  )
}

function QueueRail({
  items,
  selectedQueueId,
  currentUserId,
  isLoading,
  isFetching,
  hasNextPage,
  isFetchingNextPage,
  onSelect,
  onLoadMore,
}: {
  items: UnifiedOperatorQueueItem[]
  selectedQueueId: string | null
  currentUserId?: number
  isLoading: boolean
  isFetching: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  onSelect: (item: UnifiedOperatorQueueItem) => void
  onLoadMore: () => void
}) {
  return (
    <section className="operator-queue-list" aria-label="统一操作队列" aria-busy={isLoading || isFetching}>
      <div className="operator-section-head compact">
        <div>
          <h2>统一队列</h2>
          <p>人工接管、客服工单和运营派发使用同一任务入口。</p>
        </div>
        {isFetching && !isLoading ? <Badge>刷新中</Badge> : null}
      </div>
      {isLoading ? <EmptyState title="正在加载队列" description="正在验证工作范围和可见任务。" /> : null}
      {!isLoading && !items.length ? (
        <EmptyState title="当前范围没有任务" description="请检查范围和筛选条件；空队列不代表所有业务已经完成。" />
      ) : null}
      <div className="operator-queue-items">
        {items.map((item) => (
          <QueueRow
            key={item.queue_id}
            item={item}
            active={selectedQueueId === item.queue_id}
            currentUserId={currentUserId}
            onSelect={() => onSelect(item)}
          />
        ))}
      </div>
      {hasNextPage ? (
        <Button variant="secondary" loading={isFetchingNextPage} loadingLabel="加载更多…" onClick={onLoadMore}>
          加载更多任务
        </Button>
      ) : null}
    </section>
  )
}

function CaseSpine({
  item,
  memory,
  thread,
}: {
  item: UnifiedOperatorQueueItem
  memory: SupportMemoryLedger | null
  thread: WebchatThread | null
}) {
  const evidenceReady = Boolean(memory?.evidence_timeline?.length || latestCustomerClaim(thread))
  const decisionReady = Boolean(memory?.required_action || memory?.next_actions?.length || thread?.handoff)
  const actionStarted = timelineHas(memory, ['queued', 'submitted', 'accepted', 'processing', 'completed', 'failed'])
  const operationalComplete = timelineHas(memory, ['operational_completed'])
  const customerNotified = timelineHas(memory, ['customer_notified', 'delivered'])
  const states = [
    { label: '范围', state: 'complete', detail: `${item.country_code} · ${item.channel_key}` },
    { label: '证据', state: evidenceReady ? 'complete' : 'blocked', detail: evidenceReady ? '已有案例依据' : '尚无可验证依据' },
    { label: '判断', state: decisionReady ? 'current' : 'blocked', detail: decisionReady ? '已有待办或建议' : '需要人工判断' },
    { label: '动作', state: actionStarted ? 'current' : 'pending', detail: actionStarted ? '已有动作记录' : '尚未请求动作' },
    { label: '运营结果', state: operationalComplete ? 'complete' : 'blocked', detail: operationalComplete ? '已有运营完成证据' : '尚未验证运营完成' },
    { label: '客户通知', state: customerNotified ? 'complete' : 'blocked', detail: customerNotified ? '已有通知回执' : '尚无可靠通知回执' },
    { label: '结案或观察', state: 'blocked', detail: '尚不能判定安全结案' },
  ] as const
  return (
    <section className="operator-case-spine" aria-labelledby="case-spine-title">
      <div className="operator-section-head compact">
        <div>
          <h2 id="case-spine-title">Case Spine</h2>
          <p>范围 → 证据 → 判断 → 动作 → 运营结果 → 客户通知 → 结案或观察</p>
        </div>
        <Badge tone="warning">结案受阻</Badge>
      </div>
      <ol>
        {states.map((stage, index) => (
          <li key={stage.label} className={`is-${stage.state}`}>
            <span className="operator-spine-index" aria-hidden="true">{index + 1}</span>
            <span>
              <strong>{stage.label}</strong>
              <small>{stage.detail}</small>
            </span>
          </li>
        ))}
      </ol>
      <div className="operator-blocker" role="status">
        <strong>尚不能判定安全结案</strong>
        <p>当前版本可以展示来源状态和已记录结果，但缺少完整业务结果与生命周期权威时，不会把来源终态显示为安全结案。</p>
      </div>
    </section>
  )
}

function EvidenceCard({ item }: { item: SupportMemoryTimelineItem }) {
  const presentation = evidencePresentation(item)
  return (
    <article className={`operator-evidence-card ${presentation.className || ''}`}>
      <div className="operator-evidence-card__head">
        <Badge tone={presentation.tone}>{presentation.label}</Badge>
        {item.created_at ? <time>{formatDateTime(item.created_at)}</time> : null}
      </div>
      <strong>{sanitizeDisplayText(item.label || item.kind || '系统记录')}</strong>
      <p>{presentation.detail}</p>
      {item.status ? <small>记录状态：{sanitizeDisplayText(item.status)}</small> : null}
      {item.summary && Object.keys(item.summary).length ? (
        <TechnicalDetails title="记录详情" summary="受控技术信息">
          <pre>{JSON.stringify(item.summary, null, 2)}</pre>
        </TechnicalDetails>
      ) : null}
    </article>
  )
}

function EvidencePanel({
  memory,
  thread,
}: {
  memory: SupportMemoryLedger | null
  thread: WebchatThread | null
}) {
  const claim = latestCustomerClaim(thread)
  const evidence = memory?.evidence_timeline ?? []
  return (
    <section className="operator-evidence" aria-labelledby="operator-evidence-title">
      <div className="operator-section-head compact">
        <div>
          <h2 id="operator-evidence-title">事实与依据</h2>
          <p>事实、客户主张、知识、AI、人工决定和系统事件不会混为一类。</p>
        </div>
        <Badge>{evidence.length} 条记录</Badge>
      </div>
      {memory?.missing_fields?.length ? (
        <div className="operator-missing-facts">
          <strong>仍缺少的信息</strong>
          <ul>{memory.missing_fields.map((field) => <li key={field}>{sanitizeDisplayText(field)}</li>)}</ul>
        </div>
      ) : null}
      {claim ? (
        <article className="operator-evidence-card is-claim">
          <div className="operator-evidence-card__head">
            <Badge tone="warning">客户主张</Badge>
            {claim.created_at ? <time>{formatDateTime(claim.created_at)}</time> : null}
          </div>
          <strong>客户最近说明</strong>
          <p>{sanitizeDisplayText(claim.body_text || claim.body)}</p>
          <small>客户表述需要通过权威运营来源核实。</small>
        </article>
      ) : null}
      {evidence.length ? evidence.slice(0, 20).map((item) => <EvidenceCard key={item.source_id || `${item.kind}-${item.created_at}`} item={item} />) : null}
      {!claim && !evidence.length ? (
        <EmptyState
          title="暂无案例证据"
          description="当前来源没有可展示的事实或记录。请先获取权威信息，不要依据空白状态承诺结果。"
        />
      ) : null}
      <div className="operator-evidence-legend" aria-label="证据分类说明">
        {['事实与依据', '客户主张', '知识与政策', 'AI 建议', '人工决定', '系统事件', '动作结果', '客户通知回执'].map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
    </section>
  )
}

function OutcomeTimeline({ memory }: { memory: SupportMemoryLedger | null }) {
  const outcomeItems = (memory?.evidence_timeline ?? []).filter((item) => {
    const haystack = `${item.kind || ''} ${item.label || ''} ${item.status || ''}`.toLowerCase()
    return ['outbound', 'work_order', 'address_update', 'cancel', 'dispatch', 'action'].some((marker) => haystack.includes(marker))
  })
  return (
    <section className="operator-outcomes" aria-labelledby="operator-outcome-title">
      <div className="operator-section-head compact">
        <div>
          <h2 id="operator-outcome-title">动作与结果</h2>
          <p>请求、技术处理、运营完成、客户通知和业务结果分层显示。</p>
        </div>
      </div>
      {outcomeItems.length ? (
        <ol className="operator-outcome-list">
          {outcomeItems.slice(0, 12).map((item) => {
            const presentation = outcomePresentation(item.status, item.label)
            return (
              <li key={item.source_id || `${item.kind}-${item.created_at}`}>
                <span className={`operator-outcome-dot is-${presentation.tone}`} aria-hidden="true" />
                <div>
                  <strong>{presentation.label}</strong>
                  <p>{presentation.detail || '当前记录不足以判断业务结果。'}</p>
                  {item.created_at ? <time>{formatDateTime(item.created_at)}</time> : null}
                </div>
              </li>
            )
          })}
        </ol>
      ) : (
        <EmptyState title="暂无可验证动作结果" description="发起动作后，这里会持续显示排队、技术处理、运营结果和客户通知状态。" />
      )}
      <div className="operator-outcome-scale" aria-label="动作结果层级">
        {[
          ['请求已排队', '请求进入后台队列'],
          ['技术处理完成', '系统或 Provider 技术步骤结束'],
          ['运营已完成', '存在明确运营完成证据'],
          ['已通知客户', '存在可靠渠道通知回执'],
          ['业务结果已确认', '满足业务结果合同'],
          ['需要修复', '失败、矛盾或重试耗尽'],
        ].map(([label, detail]) => <span key={label}><strong>{label}</strong><small>{detail}</small></span>)}
      </div>
    </section>
  )
}

function ConversationPanel({
  item,
  thread,
  isLoading,
  error,
  capabilities,
  onRefresh,
}: {
  item: UnifiedOperatorQueueItem
  thread: WebchatThread | null
  isLoading: boolean
  error: unknown
  capabilities: Set<string>
  onRefresh: () => Promise<void>
}) {
  const [reply, setReply] = useState('')
  const [confirmReview, setConfirmReview] = useState(false)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const canReply = Boolean(thread && item.ticket_id && hasCapability(capabilities, 'outbound.send') && thread.handoff?.can_reply !== false)
  const replyMutation = useMutation({
    mutationFn: () => {
      if (!item.ticket_id) throw new Error('当前案例没有可回复的 Ticket')
      return operatorWorkspaceApi.reply(item.ticket_id, reply.trim(), confirmReview)
    },
    onSuccess: async () => {
      setReply('')
      setConfirmReview(false)
      await onRefresh()
    },
    onError: (mutationError) => {
      if (replyMutationSafety(mutationError)) setConfirmReview(true)
    },
  })

  useEffect(() => {
    if (!messagesRef.current) return
    messagesRef.current.scrollTop = messagesRef.current.scrollHeight
  }, [thread?.messages.length])

  const mutationSafety = replyMutationSafety(replyMutation.error)
  const disabledReason = !thread
    ? '当前案例没有可用会话'
    : !hasCapability(capabilities, 'outbound.send')
      ? '当前权限不允许发送客户消息'
      : thread.handoff?.can_reply === false
        ? '请先接管案例，或等待当前 AI 处理完成'
        : !reply.trim()
          ? '请先输入回复内容'
          : ''

  return (
    <section id="workspace-conversation" className="operator-conversation" aria-labelledby="operator-conversation-title">
      <div className="operator-section-head compact">
        <div>
          <h2 id="operator-conversation-title">客户沟通</h2>
          <p>消息显示渠道送达状态；出现在页面中不等于客户已经收到。</p>
        </div>
        {thread?.marked_unread ? <Badge tone="warning">标记未读</Badge> : null}
      </div>
      {isLoading ? <EmptyState title="正在加载会话" description="正在读取当前案例的沟通记录。" /> : null}
      {error ? <ErrorSummary title="会话暂不可用" errors={[errorCopy(error, '请稍后重试')]} /> : null}
      {!isLoading && !thread ? (
        <EmptyState
          title="当前案例没有可用会话"
          description="Ticket 或 Dispatch 仍可作为案例处理，但客户沟通功能需要已授权的会话来源。"
        />
      ) : null}
      {thread ? (
        <>
          <div className="operator-messages" ref={messagesRef} aria-live="polite">
            {thread.messages.map((message) => {
              const delivery = messageDeliveryPresentation(message.delivery_status)
              return (
                <article key={message.id} className={`operator-message is-${message.direction}`}>
                  <header>
                    <strong>{sanitizeDisplayText(message.author_label || directionLabel(message.direction))}</strong>
                    {message.created_at ? <time>{formatDateTime(message.created_at)}</time> : null}
                  </header>
                  <p>{sanitizeDisplayText(message.body_text || message.body)}</p>
                  {isOutboundMessage(message) ? (
                    <footer aria-label="送达状态">
                      <Badge tone={delivery.tone}>{delivery.label}</Badge>
                      {delivery.detail ? <span>{delivery.detail}</span> : null}
                    </footer>
                  ) : null}
                </article>
              )
            })}
            {!thread.messages.length ? <EmptyState title="暂无消息" description="该会话尚无可显示内容。" /> : null}
          </div>
          {mutationSafety ? (
            <ErrorSummary
              title="回复需要人工复核"
              errors={mutationSafety.reasons.length ? mutationSafety.reasons : ['请检查内容后再次确认发送']}
            />
          ) : null}
          {replyMutation.isError && !mutationSafety ? <ErrorSummary title="发送失败" errors={[errorCopy(replyMutation.error, '请稍后重试')]} /> : null}
          <form
            className="operator-reply"
            onSubmit={(event) => {
              event.preventDefault()
              if (canReply && reply.trim()) replyMutation.mutate()
            }}
          >
            <Field
              label="回复客户"
              description="回复会经过服务端权限、事实证据和安全检查。"
              disabledReason={disabledReason || undefined}
            >
              <Textarea
                value={reply}
                onChange={(event) => {
                  setReply(event.target.value)
                  setConfirmReview(false)
                }}
                rows={4}
                placeholder="输入清晰、可验证的客户回复…"
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

function replyMutationSafety(error: unknown) {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') return null
  const detail = error.detail as { safety?: { reasons?: string[] } }
  return detail.safety ? { reasons: detail.safety.reasons ?? [] } : null
}

function actionDisabledReason({
  action,
  item,
  capabilities,
  waybill,
  caller,
  description,
  whatsappPhone,
}: {
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
  if (action === 'waybill_lookup') {
    if (!caller.trim()) return '缺少客户电话'
    return ''
  }
  if (!waybill.trim()) return '缺少运单'
  if (!caller.trim()) return '缺少客户电话'
  if (action === 'work_order' && !hasCapability(capabilities, 'tool:speedaf.work_order.create:write')) return '当前权限不允许创建催派工单'
  if (action === 'address_update' && !hasCapability(capabilities, 'tool:speedaf.order.update_address:write')) return '当前权限不允许更新联系号码'
  if (action === 'cancel' && !hasCapability(capabilities, 'tool:speedaf.order.cancel:write')) return '当前权限不允许请求取消'
  if (action === 'work_order' && !description.trim()) return '缺少催派说明'
  if (action === 'address_update' && !whatsappPhone.trim()) return '缺少确认后的联系号码'
  return ''
}

function ActionPanel({
  item,
  thread,
  capabilities,
  onRefresh,
}: {
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
  const [cancelPreview, setCancelPreview] = useState<CancelPreview | null>(null)

  useEffect(() => {
    setAction('none')
    setWaybill('')
    setCaller(thread?.visitor?.phone || '')
    setWhatsappPhone(thread?.visitor?.phone || '')
    setCountryCode(item.country_code || 'CH')
    setDescription('')
    setCancelPreview(null)
  }, [item.queue_id, item.country_code, thread?.visitor?.phone])

  const invalidate = async () => {
    await onRefresh()
  }

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
    onSuccess: invalidate,
  })

  const actionMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      if (action === 'waybill_lookup') {
        const result = await supportApi.querySpeedafWaybills(item.ticket_id, {
          callerID: caller.trim(),
          countryCode: countryCode.trim().toUpperCase(),
        })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      if (action === 'work_order') {
        const result = await supportApi.createSpeedafWorkOrder(item.ticket_id, {
          waybillCode: waybill.trim().toUpperCase(),
          callerID: caller.trim(),
          workOrderType: 'WT0103-05',
          description: description.trim(),
        })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      if (action === 'address_update') {
        const result = await supportApi.submitSpeedafAddressUpdate(item.ticket_id, {
          waybillCode: waybill.trim().toUpperCase(),
          callerID: caller.trim(),
          whatsAppPhone: whatsappPhone.trim(),
        })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      throw new Error('请先选择可执行动作')
    },
    onSuccess: invalidate,
  })

  const cancelPreviewMutation = useMutation({
    mutationFn: async () => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      return supportApi.previewSpeedafCancel(item.ticket_id, {
        waybillCode: waybill.trim().toUpperCase(),
        callerID: caller.trim(),
        reasonCode,
      })
    },
    onSuccess: (result) => setCancelPreview(result),
  })

  const cancelConfirmMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      const result = await supportApi.confirmSpeedafCancel(item.ticket_id, {
        waybillCode: waybill.trim().toUpperCase(),
        callerID: caller.trim(),
        reasonCode,
        confirmToken: cancelPreview?.confirmToken || '',
      })
      return { kind: 'cancel', result: result as unknown as Record<string, unknown> }
    },
    onSuccess: invalidate,
  })

  const disabledReason = actionDisabledReason({ action, item, capabilities, waybill, caller, description, whatsappPhone })
  const busy = handoffMutation.isPending || actionMutation.isPending || cancelPreviewMutation.isPending || cancelConfirmMutation.isPending
  const actionError = handoffMutation.error || actionMutation.error || cancelPreviewMutation.error || cancelConfirmMutation.error
  const envelope = actionMutation.data || cancelConfirmMutation.data
  const resultRecord = envelope?.result ?? {}
  const resultPresentation = envelope ? outcomePresentation(resultRecord.status, resultRecord.message) : null
  const candidates = Array.isArray(resultRecord.candidates) ? resultRecord.candidates.map(safeRecord) : []

  const handoff = thread?.handoff
  const takeoverAvailable = Boolean(handoff?.can_accept || handoff?.can_force_takeover || (!handoff && item.source_type === 'handoff'))
  const conversationReason = thread ? '' : '当前案例没有可用会话'
  const permissionReason = hasCapability(
    capabilities,
    'webchat.handoff.accept',
    'webchat.handoff.force_takeover',
    'webchat.handoff.release',
    'webchat.handoff.resume_ai',
  ) ? '' : '当前权限不允许接管或恢复会话'

  return (
    <section id="workspace-actions" className="operator-actions-panel" aria-labelledby="operator-actions-title">
      <div className="operator-section-head compact">
        <div>
          <h2 id="operator-actions-title">下一步动作</h2>
          <p>只展示现有受控动作。不可执行原因会直接说明。</p>
        </div>
        <Badge tone="warning">服务端最终授权</Badge>
      </div>

      <div className="operator-action-group">
        <h3>案例接管</h3>
        {conversationReason ? <p className="operator-disabled-reason"><strong>不可执行原因：</strong>{conversationReason}</p> : null}
        {permissionReason ? <p className="operator-disabled-reason"><strong>不可执行原因：</strong>{permissionReason}</p> : null}
        <div className="operator-button-row">
          {takeoverAvailable ? (
            <Button
              variant="primary"
              loading={handoffMutation.isPending}
              disabled={Boolean(conversationReason || permissionReason)}
              onClick={() => handoffMutation.mutate(handoff?.can_accept ? 'accept' : 'force')}
            >
              接管案例
            </Button>
          ) : null}
          {handoff?.can_decline ? <Button variant="secondary" onClick={() => handoffMutation.mutate('decline')}>暂不接管</Button> : null}
          {handoff?.can_release ? <Button variant="ghost" onClick={() => handoffMutation.mutate('release')}>释放案例</Button> : null}
          {handoff?.can_resume_ai ? <Button variant="ghost" onClick={() => handoffMutation.mutate('resume')}>恢复 AI</Button> : null}
        </div>
        {handoff?.reason_text || handoff?.recommended_agent_action ? (
          <div className="operator-action-context">
            {handoff.reason_text ? <p><strong>接管原因：</strong>{sanitizeDisplayText(handoff.reason_text)}</p> : null}
            {handoff.recommended_agent_action ? <p><strong>建议动作：</strong>{sanitizeDisplayText(handoff.recommended_agent_action)}</p> : null}
            {typeof handoff.waiting_seconds === 'number' ? <p><strong>已等待：</strong>{Math.ceil(handoff.waiting_seconds / 60)} 分钟</p> : null}
          </div>
        ) : null}
      </div>

      <div className="operator-action-group">
        <h3>Speedaf 受控动作</h3>
        <Field label="选择动作" description="只读查询与高影响写入动作明确区分，不默认选择高影响动作。">
          <Select
            value={action}
            onChange={(event) => {
              setAction(event.target.value as SpeedafActionKind)
              setCancelPreview(null)
              actionMutation.reset()
              cancelConfirmMutation.reset()
            }}
          >
            <option value="none">请选择动作</option>
            <option value="waybill_lookup">电话查单（只读）</option>
            <option value="work_order">创建催派工单</option>
            <option value="address_update">提交联系号码更新</option>
            <option value="cancel">取消预检与确认</option>
          </Select>
        </Field>
        {action !== 'none' ? (
          <>
            <div className="operator-form-grid">
              {action !== 'waybill_lookup' ? (
                <Field label="运单" required hint="缺少运单时无法执行写入动作。">
                  <Input value={waybill} onChange={(event) => setWaybill(event.target.value.toUpperCase())} autoComplete="off" placeholder="输入完整运单号" />
                </Field>
              ) : null}
              <Field label="客户电话" required hint="用于服务端核验；不会作为长期客户记忆。">
                <Input type="tel" inputMode="tel" value={caller} onChange={(event) => setCaller(event.target.value)} autoComplete="off" placeholder="输入客户电话" />
              </Field>
            </div>
            {action === 'waybill_lookup' ? (
              <Field label="国家代码" required>
                <Input value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} autoComplete="off" />
              </Field>
            ) : null}
            {action === 'work_order' ? (
              <Field label="催派说明" required description="说明客户诉求和期望的运营处理。">
                <Textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} />
              </Field>
            ) : null}
            {action === 'address_update' ? (
              <Field label="确认后的联系号码" required description="该动作提交的是确认流程，不代表地址已经最终修改。">
                <Input type="tel" inputMode="tel" value={whatsappPhone} onChange={(event) => setWhatsappPhone(event.target.value)} />
              </Field>
            ) : null}
            {action === 'cancel' ? (
              <Field label="取消原因" required>
                <Select value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); setCancelPreview(null) }}>
                  <option value="CC01">派送太慢</option>
                  <option value="CC02">快递员服务问题</option>
                  <option value="CC03">不支持验货</option>
                  <option value="CC04">不支持部分签收</option>
                  <option value="CC05">其他原因</option>
                </Select>
              </Field>
            ) : null}
          </>
        ) : null}

        {disabledReason ? <p className="operator-disabled-reason"><strong>不可执行原因：</strong>{disabledReason}</p> : null}
        {actionError ? <ErrorSummary title="动作未完成" errors={[errorCopy(actionError, '请稍后重试')]} /> : null}

        {candidates.length ? (
          <div className="operator-candidates">
            <strong>候选运单</strong>
            {candidates.map((candidate) => (
              <div key={textValue(candidate.waybillCode)}>
                <span>{sanitizeDisplayText(textValue(candidate.waybillCode))}</span>
                <Button
                  size="sm"
                  onClick={() => {
                    setWaybill(textValue(candidate.waybillCode))
                    setAction('work_order')
                    actionMutation.reset()
                  }}
                >
                  填入催派
                </Button>
              </div>
            ))}
          </div>
        ) : null}

        {cancelPreview ? (
          <div className={`operator-action-receipt ${cancelPreview.cancelAllowed ? 'is-neutral' : 'is-warning'}`} role="status">
            <strong>{cancelPreview.cancelAllowed ? '预检允许提交取消请求' : '当前状态不允许取消'}</strong>
            <p>{sanitizeDisplayText(cancelPreview.currentStatusLabel || cancelPreview.reasonLabel || '未返回原因')}</p>
            {cancelPreview.cancelAllowed ? <small>预检不是取消完成；确认后仍需外部系统和人工结果确认。</small> : null}
          </div>
        ) : null}

        {resultPresentation ? (
          <div className={`operator-action-receipt is-${resultPresentation.tone}`} role="status" aria-live="polite">
            <strong>{resultPresentation.label}</strong>
            <p>{resultPresentation.detail}</p>
            {numberValue(resultRecord.jobId) ? (
              <TechnicalDetails title="请求追踪" summary="技术标识">
                <code>Job #{numberValue(resultRecord.jobId)}</code>
              </TechnicalDetails>
            ) : null}
          </div>
        ) : null}

        <div className="operator-button-row">
          {action === 'cancel' ? (
            <>
              <Button
                variant="secondary"
                loading={cancelPreviewMutation.isPending}
                disabled={Boolean(disabledReason) || busy}
                onClick={() => cancelPreviewMutation.mutate()}
              >
                先做取消预检
              </Button>
              <Button
                variant="danger"
                loading={cancelConfirmMutation.isPending}
                disabled={!cancelPreview?.cancelAllowed || !cancelPreview.confirmToken || busy}
                onClick={() => cancelConfirmMutation.mutate()}
              >
                确认提交取消请求
              </Button>
            </>
          ) : action !== 'none' ? (
            <Button
              variant={action === 'work_order' ? 'primary' : 'secondary'}
              loading={actionMutation.isPending}
              disabled={Boolean(disabledReason) || busy}
              onClick={() => actionMutation.mutate()}
            >
              {action === 'waybill_lookup'
                ? '查询运单'
                : action === 'work_order'
                  ? '创建催派工单'
                  : '提交联系号码更新'}
            </Button>
          ) : null}
        </div>
      </div>
    </section>
  )
}

function CaseHeader({
  item,
  currentUserId,
}: {
  item: UnifiedOperatorQueueItem
  currentUserId?: number
}) {
  const source = queueSourcePresentation(item.source_type)
  const status = sourceStatusPresentation(item.source_status)
  const owner = ownerPresentation(item.owner, currentUserId)
  const sla = slaPresentation(item.sla)
  const retry = retryPresentation(item.retry)
  return (
    <header className="operator-case-header">
      <div>
        <div className="operator-case-kicker">{source.label} · {item.country_code} · {item.channel_key}</div>
        <h1>{item.case_key || item.queue_id}</h1>
        <p>来源记录 {item.source_type}:{item.source_id}{item.ticket_id ? ` · Ticket #${item.ticket_id}` : ''}</p>
      </div>
      <div className="operator-case-statuses" aria-label="案例状态">
        <PresentationBadge presentation={status} />
        <PresentationBadge presentation={owner} />
        <PresentationBadge presentation={sla} />
        {item.source_type === 'dispatch' ? <PresentationBadge presentation={retry} /> : null}
        {item.reopened ? <PresentationBadge presentation={{ label: '已重新打开', detail: '需要重新核实当前业务结果', tone: 'warning' }} /> : null}
      </div>
    </header>
  )
}

function AppNavigation({
  capabilities,
}: {
  capabilities: Set<string>
}) {
  const links = [
    {
      label: '工作台',
      href: '/workspace',
      visible: hasCapability(capabilities, 'operator_queue.read', 'ticket.read'),
      active: true,
    },
    {
      label: '知识',
      href: '/webchat?tab=knowledge',
      visible: hasCapability(capabilities, 'ai_config.read', 'ai_config.manage'),
      active: false,
    },
    {
      label: '渠道管理',
      href: '/webchat?tab=channels',
      visible: hasCapability(capabilities, 'channel_account.manage'),
      active: false,
    },
    {
      label: '运行与审计',
      href: '/webchat?tab=runtime',
      visible: hasCapability(capabilities, 'runtime.manage'),
      active: false,
    },
  ].filter((link) => link.visible)

  return (
    <nav className="operator-app-nav" aria-label="Nexus OSR 主导航">
      {links.map((link) => (
        <a key={link.label} href={link.href} aria-current={link.active ? 'page' : undefined}>
          {link.label}
        </a>
      ))}
    </nav>
  )
}

export function OperatorWorkspacePage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const [scopeDraft, setScopeDraft] = useState<WorkspaceScope>(() => loadWorkspaceScope())
  const [scope, setScope] = useState<WorkspaceScope | null>(() => {
    const initial = loadWorkspaceScope()
    return initial.tenantKey && initial.countryCode && initial.channelKey ? initial : null
  })
  const [filters, setFilters] = useState<WorkspaceFilters>(defaultFilters)
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(() => initialQueueId())
  const [mobileView, setMobileView] = useState<WorkspaceMobileView>('queue')

  useEffect(() => {
    document.title = '操作员工作台 · Nexus OSR'
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    if (selectedQueueId) url.searchParams.set('queue', selectedQueueId)
    else url.searchParams.delete('queue')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }, [selectedQueueId])

  const canReadQueue = hasCapability(capabilities, 'operator_queue.read')
  const scopeReady = Boolean(scope?.tenantKey && scope.countryCode && scope.channelKey)

  const queue = useInfiniteQuery({
    queryKey: ['operatorWorkspaceQueue', scope, filters],
    queryFn: ({ pageParam }) => operatorWorkspaceApi.unifiedQueue(scope as WorkspaceScope, filters, pageParam as string | null),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
    enabled: Boolean(session.data && canReadQueue && scopeReady),
    retry: false,
    refetchInterval: 15000,
  })

  const queueItems = useMemo(
    () => queue.data?.pages.flatMap((page) => page.items) ?? [],
    [queue.data?.pages],
  )
  const selectedItem = useMemo(
    () => queueItems.find((item) => item.queue_id === selectedQueueId) ?? queueItems[0] ?? null,
    [queueItems, selectedQueueId],
  )

  useEffect(() => {
    if (!selectedQueueId && selectedItem) setSelectedQueueId(selectedItem.queue_id)
  }, [selectedItem, selectedQueueId])

  const thread = useQuery({
    queryKey: ['operatorWorkspaceThread', selectedItem?.queue_id, selectedItem?.source_links.conversation],
    queryFn: () => operatorWorkspaceApi.conversationThread(selectedItem?.source_links.conversation || ''),
    enabled: Boolean(selectedItem?.source_links.conversation),
    retry: false,
    refetchInterval: selectedItem?.source_links.conversation ? 5000 : false,
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

  const applyScope = () => {
    const normalizedScope = {
      tenantKey: scopeDraft.tenantKey.trim(),
      countryCode: scopeDraft.countryCode.trim().toUpperCase(),
      channelKey: scopeDraft.channelKey.trim().toLowerCase(),
    }
    saveWorkspaceScope(normalizedScope)
    setScope(normalizedScope)
    setSelectedQueueId(null)
    setMobileView('queue')
  }

  const selectItem = (item: UnifiedOperatorQueueItem) => {
    setSelectedQueueId(item.queue_id)
    setMobileView('case')
  }

  const handleLogout = () => {
    logout()
    navigate({ to: '/login', replace: true })
  }

  const memory = supportMemoryFromThread(thread.data)
  const appliedScopeMatches = Boolean(
    scope
    && scope.tenantKey === scopeDraft.tenantKey.trim()
    && scope.countryCode === scopeDraft.countryCode.trim().toUpperCase()
    && scope.channelKey === scopeDraft.channelKey.trim().toLowerCase(),
  )

  return (
    <main className={`operator-workspace is-mobile-${mobileView}`} data-testid="operator-workspace">
      <header className="operator-app-header">
        <div className="operator-brand">
          <span>Nexus OSR</span>
          <strong>操作员工作台</strong>
        </div>
        <AppNavigation capabilities={capabilities} />
        <div className="operator-user">
          <span>{session.data?.display_name || session.data?.username || '操作员'}</span>
          <Button variant="ghost" onClick={handleLogout}>退出</Button>
        </div>
      </header>

      <div className="operator-mobile-nav" role="navigation" aria-label="移动端工作区">
        {mobileViews.map((view) => (
          <button
            key={view.value}
            type="button"
            className={mobileView === view.value ? 'is-active' : ''}
            aria-pressed={mobileView === view.value}
            onClick={() => {
              setMobileView(view.value)
              const targetId = view.value === 'queue'
                ? 'workspace-queue'
                : view.value === 'case'
                  ? 'workspace-case'
                  : view.value === 'conversation'
                    ? 'workspace-conversation'
                    : 'workspace-actions'
              window.requestAnimationFrame(() => document.getElementById(targetId)?.scrollIntoView({ block: 'start' }))
            }}
          >
            {view.label}
          </button>
        ))}
      </div>

      {!session.data && session.isLoading ? (
        <section className="operator-session-state"><EmptyState title="正在验证身份" description="正在读取当前用户和权限。" /></section>
      ) : null}
      {session.isError ? (
        <section className="operator-session-state">
          <ErrorSummary title="无法读取当前用户" errors={[errorCopy(session.error, '请重新登录')]} />
        </section>
      ) : null}
      {session.data && !canReadQueue ? (
        <section className="operator-session-state">
          <EmptyState title="当前权限不允许访问操作队列" description="需要 operator_queue.read 权限。系统不会通过前端绕过后端授权。" />
        </section>
      ) : null}

      {session.data && canReadQueue ? (
        <div className="operator-layout">
          <aside id="workspace-queue" className="operator-queue-pane">
            <ScopeEditor
              draft={scopeDraft}
              onChange={setScopeDraft}
              onApply={applyScope}
              applied={appliedScopeMatches}
            />
            <QueueFilters filters={filters} onChange={(next) => { setFilters(next); setSelectedQueueId(null) }} />
            {scopeReady ? (
              <>
                {queue.isError ? (
                  <ErrorSummary
                    title="统一队列不可用"
                    errors={[errorCopy(queue.error, '请检查 Tenant、国家、渠道和当前授权')]}
                    action={<Button onClick={() => queue.refetch()}>重新加载</Button>}
                  />
                ) : null}
                <QueueRail
                  items={queueItems}
                  selectedQueueId={selectedItem?.queue_id ?? null}
                  currentUserId={session.data.id}
                  isLoading={queue.isLoading}
                  isFetching={queue.isFetching}
                  hasNextPage={Boolean(queue.hasNextPage)}
                  isFetchingNextPage={queue.isFetchingNextPage}
                  onSelect={selectItem}
                  onLoadMore={() => queue.fetchNextPage()}
                />
              </>
            ) : (
              <EmptyState title="先应用工作范围" description="工作台不会在 Tenant、国家和渠道未明确时读取任务。" />
            )}
          </aside>

          <section id="workspace-case" className="operator-case-pane" aria-label="当前案例">
            {selectedItem ? (
              <>
                <CaseHeader item={selectedItem} currentUserId={session.data.id} />
                <CaseSpine item={selectedItem} memory={memory} thread={thread.data ?? null} />
                {sourceRecord.data && !thread.data ? (
                  <section className="operator-source-summary">
                    <div className="operator-section-head compact">
                      <div>
                        <h2>来源记录摘要</h2>
                        <p>该案例没有 WebChat 会话，但仍可作为 Ticket 或 Dispatch 工作处理。</p>
                      </div>
                    </div>
                    <dl>
                      <div><dt>标题</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.data.title) || '未提供')}</dd></div>
                      <div><dt>状态</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.data.status) || selectedItem.source_status)}</dd></div>
                      <div><dt>优先级</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.data.priority) || selectedItem.priority)}</dd></div>
                    </dl>
                  </section>
                ) : null}
                {sourceRecord.isError ? <ErrorSummary title="来源详情暂不可用" errors={[errorCopy(sourceRecord.error, '仍可基于队列摘要继续分诊')]} /> : null}
                <EvidencePanel memory={memory} thread={thread.data ?? null} />
                <ConversationPanel
                  item={selectedItem}
                  thread={thread.data ?? null}
                  isLoading={thread.isLoading}
                  error={thread.error}
                  capabilities={capabilities}
                  onRefresh={refreshSelected}
                />
              </>
            ) : (
              <EmptyState
                title="选择一个任务开始处理"
                description="从统一队列选择人工接管、客服工单或运营派发任务。"
              />
            )}
          </section>

          <aside className="operator-context-pane" aria-label="案例动作与结果">
            {selectedItem ? (
              <>
                <section className="operator-current-task">
                  <div className="operator-section-head compact">
                    <div>
                      <h2>当前任务</h2>
                      <p>先理解阻塞原因，再执行一个主要动作。</p>
                    </div>
                  </div>
                  <strong>{sanitizeDisplayText(memory?.required_action || memory?.next_actions?.[0]?.label || '核实当前事实并决定下一步')}</strong>
                  {memory?.current_intent ? <p>识别意图：{sanitizeDisplayText(memory.current_intent)}</p> : null}
                  <small>前端建议不替代服务端权限、政策和结果权威。</small>
                </section>
                <ActionPanel
                  item={selectedItem}
                  thread={thread.data ?? null}
                  capabilities={capabilities}
                  onRefresh={refreshSelected}
                />
                <OutcomeTimeline memory={memory} />
                <TechnicalDetails title="技术与来源详情" summary="默认收起">
                  <dl className="operator-technical-list">
                    <div><dt>Queue ID</dt><dd><code>{selectedItem.queue_id}</code></dd></div>
                    <div><dt>来源链接</dt><dd><pre>{JSON.stringify(selectedItem.source_links, null, 2)}</pre></dd></div>
                    <div><dt>安全范围</dt><dd><pre>{JSON.stringify(queue.data?.pages[0]?.scope || {}, null, 2)}</pre></dd></div>
                  </dl>
                </TechnicalDetails>
              </>
            ) : (
              <EmptyState title="暂无动作" description="选择案例后显示允许动作、不可执行原因和持久结果。" />
            )}
          </aside>
        </div>
      ) : null}
    </main>
  )
}
