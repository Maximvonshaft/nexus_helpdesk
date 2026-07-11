import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { ApiError, supportApi } from '@/lib/supportApi'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { useLogout, useSession } from '@/hooks/useAuth'
import type {
  BadgeTone,
  ChannelAccount,
  KnowledgeItem,
  SupportConversation,
  SupportConversationMessage,
  SupportMemoryLedger,
} from '@/lib/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'

type InboxView = 'open' | 'needs_human' | 'mine' | 'all'
type ChannelFilter = 'all' | 'webchat' | 'whatsapp'
type WorkbenchView = 'conversations' | 'knowledge' | 'channels' | 'runtime'
type SpeedafActionKind = 'waybill_lookup' | 'work_order' | 'address_update' | 'cancel'
type KnowledgeStatusFilter = 'active' | 'draft' | 'archived' | 'all'
type KnowledgeKindFilter = 'all' | 'business_fact' | 'faq' | 'policy' | 'sop' | 'document'

type KnowledgeDraft = {
  item_key: string
  title: string
  fact_question: string
  fact_answer: string
  fact_aliases: string
  summary: string
  status: string
  channel: string
  audience_scope: string
  language: string
  priority: string
  knowledge_kind: string
  answer_mode: string
}

const viewOptions: Array<{ value: InboxView; label: string }> = [
  { value: 'open', label: '处理中' },
  { value: 'needs_human', label: '待人工' },
  { value: 'mine', label: '我的' },
  { value: 'all', label: '全部' },
]

const channelOptions: Array<{ value: ChannelFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'webchat', label: 'WebChat' },
  { value: 'whatsapp', label: 'WhatsApp' },
]

const workbenchViews: Array<{ value: WorkbenchView; label: string }> = [
  { value: 'conversations', label: '会话' },
  { value: 'knowledge', label: '知识' },
  { value: 'channels', label: '渠道' },
  { value: 'runtime', label: '运行' },
]

const knowledgeKindOptions: Array<{ value: KnowledgeKindFilter; label: string; description: string }> = [
  { value: 'all', label: '全部分类', description: '查看所有客服知识' },
  { value: 'business_fact', label: '客服问答', description: '客户常问问题和标准事实' },
  { value: 'faq', label: '常见问题', description: '高频问题和服务说明' },
  { value: 'policy', label: '规则政策', description: '必须严格遵守的服务规则' },
  { value: 'sop', label: '处理流程', description: '客服和 AI 可参考的操作步骤' },
  { value: 'document', label: '资料文档', description: '导入资料、长文档和待整理内容' },
]

function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? value as T : fallback
}

function readSupportWorkbenchSearch() {
  if (typeof window === 'undefined') {
    return { activeView: 'conversations' as WorkbenchView, view: 'open' as InboxView, channel: 'all' as ChannelFilter, query: '', sessionKey: null as string | null }
  }
  const params = new URLSearchParams(window.location.search)
  return {
    activeView: oneOf(params.get('tab'), workbenchViews.map((item) => item.value), 'conversations'),
    view: oneOf(params.get('view'), viewOptions.map((item) => item.value), 'open'),
    channel: oneOf(params.get('channel'), channelOptions.map((item) => item.value), 'all'),
    query: params.get('q') || '',
    sessionKey: params.get('session'),
  }
}

function safeTone(value: string | undefined | null, fallback: BadgeTone = 'default'): BadgeTone {
  return value === 'success' || value === 'warning' || value === 'danger' || value === 'default'
    ? value
    : fallback
}

function toneForChannel(channel: string): BadgeTone {
  if (channel === 'whatsapp') return 'success'
  if (channel === 'webchat') return 'warning'
  return 'default'
}

function toneForHealth(value: string | null | undefined): BadgeTone {
  const normalized = String(value || '').toLowerCase()
  if (['connected', 'healthy', 'ok', 'ready', 'online', 'pass', 'success'].some((token) => normalized.includes(token))) return 'success'
  if (['offline', 'failed', 'error', 'dead', 'blocked'].some((token) => normalized.includes(token))) return 'danger'
  if (['degraded', 'warning', 'pending', 'unknown', 'review'].some((token) => normalized.includes(token))) return 'warning'
  return 'default'
}

function stateLabel(item: SupportConversation) {
  if (item.needs_human) return '待人工'
  if (item.handoff_status === 'accepted') return '人工中'
  if (item.ai_pending) return 'AI中'
  if (item.status === 'closed' || item.status === 'resolved') return '已结束'
  return '打开'
}

function toneForConversation(item: SupportConversation): BadgeTone {
  if (item.needs_human) return 'danger'
  if (item.handoff_status === 'accepted') return 'success'
  if (item.ai_pending) return 'warning'
  return 'default'
}

function authorLabel(author: string | null | undefined) {
  if (author === 'customer') return '客户'
  if (author === 'agent') return '客服'
  if (author === 'ai') return 'AI'
  return '系统'
}

function compactNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '0'
}

function compactLatency(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '暂无'
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}s`
  return `${Math.max(0, Math.round(value))}ms`
}

function knowledgeStatusLabel(status: string | null | undefined) {
  if (status === 'active') return '已上线'
  if (status === 'draft') return '草稿'
  if (status === 'archived') return '已归档'
  return sanitizeDisplayText(status || '未知')
}

function knowledgeKindLabel(kind: string | null | undefined) {
  if (kind === 'business_fact') return '客服问答'
  if (kind === 'faq') return '常见问题'
  if (kind === 'policy') return '规则政策'
  if (kind === 'sop') return '处理流程'
  if (kind === 'document') return '资料文档'
  return sanitizeDisplayText(kind || '知识')
}

function knowledgeKindDescription(kind: string | null | undefined) {
  return knowledgeKindOptions.find((item) => item.value === kind)?.description || '客服知识'
}

function normalizeKnowledgeKey(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '')
    .replace(/[^a-z0-9]+$/, '')
    .slice(0, 120)
}

function createKnowledgeKey() {
  return `support.customer.${Date.now().toString(36)}`
}

function knowledgeDraftFromItem(item?: KnowledgeItem | null): KnowledgeDraft {
  if (!item) {
    return {
      item_key: createKnowledgeKey(),
      title: '',
      fact_question: '',
      fact_answer: '',
      fact_aliases: '',
      summary: '',
      status: 'draft',
      channel: 'all',
      audience_scope: 'customer',
      language: '',
      priority: '100',
      knowledge_kind: 'business_fact',
      answer_mode: 'guided_answer',
    }
  }
  return {
    item_key: item.item_key,
    title: item.title || '',
    fact_question: item.fact_question || '',
    fact_answer: item.fact_answer || item.draft_body || item.published_body || '',
    fact_aliases: (item.fact_aliases_json || []).join('\n'),
    summary: item.summary || '',
    status: item.status || 'draft',
    channel: item.channel || 'all',
    audience_scope: item.audience_scope || 'customer',
    language: item.language || '',
    priority: String(item.priority ?? 100),
    knowledge_kind: item.knowledge_kind || 'business_fact',
    answer_mode: item.answer_mode || 'guided_answer',
  }
}

function knowledgePayloadFromDraft(draft: KnowledgeDraft) {
  const question = draft.fact_question.trim()
  const answer = draft.fact_answer.trim()
  const aliases = draft.fact_aliases.split(/\r?\n/).map((item) => item.trim()).filter(Boolean).slice(0, 50)
  const draftBody = [
    question ? `Customer question: ${question}` : '',
    answer ? `Answer guidance: ${answer}` : '',
    aliases.length ? `Alternative customer wording:\n${aliases.map((item) => `- ${item}`).join('\n')}` : '',
  ].filter(Boolean).join('\n\n')
  return {
    title: draft.title.trim(),
    summary: draft.summary.trim() || null,
    status: draft.status,
    source_type: 'text',
    knowledge_kind: draft.knowledge_kind,
    channel: draft.channel === 'all' ? null : draft.channel,
    audience_scope: draft.audience_scope,
    language: draft.language.trim() || null,
    priority: Math.max(0, Math.min(10000, Number.parseInt(draft.priority, 10) || 100)),
    fact_question: question || null,
    fact_answer: answer || null,
    fact_aliases_json: aliases.length ? aliases : null,
    fact_status: draft.status === 'active' ? 'approved' : 'draft',
    answer_mode: draft.answer_mode,
    draft_body: draftBody || answer || question || null,
    draft_normalized_text: draftBody || answer || question || null,
  }
}

function aiReplySourceLabel(value: string | null | undefined) {
  const normalized = String(value || '').trim().toLowerCase()
  if (!normalized) return '暂无'
  if (normalized === 'private_ai_runtime') return '统一 AI Runtime'
  return sanitizeDisplayText(value)
}

function contactPhoneCandidate(value: string | null | undefined) {
  const text = String(value || '').trim()
  if (!/\d/.test(text)) return ''
  return text.replace(/[^\d+]/g, '')
}

function parseSafetyError(error: unknown) {
  if (!(error instanceof ApiError) || typeof error.detail !== 'object' || error.detail === null) return null
  const detail = error.detail as { safety?: { reasons?: string[]; normalized_body?: string } }
  return detail.safety ?? null
}

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function ConversationRow({ item, active, onSelect }: { item: SupportConversation; active: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`support-row${active ? ' active' : ''}`} onClick={onSelect} aria-pressed={active}>
      <span className="support-row-top">
        <span className="support-row-title">{sanitizeDisplayText(item.display_name || item.customer_contact || '客户')}</span>
        <Badge tone={toneForChannel(item.channel)}>{item.channel === 'whatsapp' ? 'WhatsApp' : 'WebChat'}</Badge>
      </span>
      <span className="support-row-preview">
        {item.latest_author ? `${authorLabel(item.latest_author)}：` : null}
        {sanitizeDisplayText(item.latest_message || item.title || '暂无消息')}
      </span>
      <span className="support-row-bottom">
        <Badge tone={toneForConversation(item)}>{stateLabel(item)}</Badge>
        <span>{item.updated_at ? formatDateTime(item.updated_at) : '未更新'}</span>
      </span>
    </button>
  )
}

function MessageBubble({ message }: { message: SupportConversationMessage }) {
  return (
    <article className={`support-message ${message.author}`}>
      <div className="support-message-head">
        <span>{authorLabel(message.author)}</span>
        <time>{message.timestamp ? formatDateTime(message.timestamp) : ''}</time>
      </div>
      <div className="support-message-body">{sanitizeDisplayText(message.body)}</div>
    </article>
  )
}

function MetricTile({ label, value, tone = 'default' }: { label: string; value: string | number; tone?: BadgeTone }) {
  return (
    <div className={`support-metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function SupportMemoryPanel({ ledger }: { ledger?: SupportMemoryLedger | null }) {
  if (!ledger) {
    return <EmptyState title="暂无记忆证据" description="当前会话还没有可展示的知识、工具或接管证据。" />
  }
  const latestTurnSummary = ledger.ai_state?.last_turn?.summary as { runtime_trace?: Record<string, unknown> } | undefined
  const runtimeTrace = latestTurnSummary?.runtime_trace
  const runtimeUsage = runtimeTrace?.runtime_usage as Record<string, unknown> | undefined
  const runtimeElapsed = typeof runtimeTrace?.elapsed_ms === 'number' ? runtimeTrace.elapsed_ms : ledger.ai_state?.last_bridge_elapsed_ms
  const evalElapsed = typeof runtimeUsage?.eval_duration_ms === 'number' ? runtimeUsage.eval_duration_ms : null
  const promptElapsed = typeof runtimeUsage?.prompt_eval_duration_ms === 'number' ? runtimeUsage.prompt_eval_duration_ms : null
  return (
    <div className="support-side-stack">
      <div className="support-fact-grid">
        <div>
          <span>运单</span>
          <strong>{ledger.tracking?.present ? `已识别 ${ledger.tracking.suffix || ''}` : '未识别'}</strong>
        </div>
        <div>
          <span>缺失字段</span>
          <strong>{ledger.missing_fields?.length ? ledger.missing_fields.join(', ') : '无'}</strong>
        </div>
        <div>
          <span>AI 耗时</span>
          <strong>{compactLatency(runtimeElapsed)}</strong>
        </div>
        <div>
          <span>AI 来源</span>
          <strong>{aiReplySourceLabel(ledger.ai_state?.last_ai_reply_source)}</strong>
        </div>
      </div>
      {runtimeTrace ? (
        <div className="support-side-note">
          <span>Runtime trace</span>
          <div className="support-runtime-trace">
            <strong>{sanitizeDisplayText(String(runtimeTrace.latency_class || 'standard'))}</strong>
            <small>{sanitizeDisplayText(String(runtimeTrace.model || 'model unknown'))}</small>
            <small>
              eval {compactLatency(evalElapsed)}
              {promptElapsed !== null ? ` · prompt ${compactLatency(promptElapsed)}` : ''}
            </small>
          </div>
        </div>
      ) : null}
      {ledger.current_intent ? (
        <div className="support-side-note">
          <span>当前意图</span>
          <strong>{sanitizeDisplayText(ledger.current_intent)}</strong>
        </div>
      ) : null}
      {ledger.next_actions?.length ? (
        <div className="support-side-note">
          <span>下一步</span>
          <div className="support-badges">
            {ledger.next_actions.slice(0, 4).map((item) => (
              <Badge key={item.key} tone={safeTone(String(item.tone || 'default'))}>{sanitizeDisplayText(item.label)}</Badge>
            ))}
          </div>
        </div>
      ) : null}
      {ledger.evidence_timeline?.length ? (
        <div className="support-side-note">
          <span>证据</span>
          <div className="support-evidence-list">
            {ledger.evidence_timeline.slice(0, 5).map((item) => (
              <div key={`${item.kind}-${item.source_id || item.created_at || item.label}`}>
                <strong>{sanitizeDisplayText(item.label || item.kind)}</strong>
                <small>{item.created_at ? formatDateTime(item.created_at) : sanitizeDisplayText(item.status || '')}</small>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

const cancelReasons = [
  { value: 'CC01', label: '派送太慢' },
  { value: 'CC02', label: '快递员服务问题' },
  { value: 'CC03', label: '不支持验货' },
  { value: 'CC04', label: '不支持部分签收' },
  { value: 'CC05', label: '其他原因' },
]

function SpeedafControlledActionsPanel({
  activeConversation,
  supportMemory,
  onDone,
}: {
  activeConversation?: SupportConversation
  supportMemory?: SupportMemoryLedger | null
  onDone: () => Promise<void>
}) {
  const initialContactPhone = contactPhoneCandidate(activeConversation?.customer_contact)
  const [action, setAction] = useState<SpeedafActionKind>('work_order')
  const [waybillCode, setWaybillCode] = useState(activeConversation?.tracking_number || '')
  const [callerID, setCallerID] = useState(initialContactPhone)
  const [countryCode, setCountryCode] = useState('CH')
  const [description, setDescription] = useState('')
  const [whatsAppPhone, setWhatsAppPhone] = useState(initialContactPhone)
  const [reasonCode, setReasonCode] = useState('CC01')
  const [cancelPreview, setCancelPreview] = useState<{ cancelAllowed: boolean; confirmToken?: string | null; currentStatusLabel?: string | null; reasonLabel?: string | null } | null>(null)

  const ticketId = activeConversation?.ticket_id
  const trimmedWaybill = waybillCode.trim().toUpperCase()
  const trimmedCaller = callerID.trim()
  const trimmedCountry = countryCode.trim().toUpperCase() || 'CH'
  const trackingHint = supportMemory?.tracking?.present && supportMemory.tracking.suffix ? `尾号 ${supportMemory.tracking.suffix}` : '手动输入'

  const afterSuccess = async () => {
    await onDone()
  }

  const waybillLookupMutation = useMutation({
    mutationFn: () => supportApi.querySpeedafWaybills(ticketId ?? 0, {
      callerID: trimmedCaller,
      countryCode: trimmedCountry,
    }),
  })
  const workOrderMutation = useMutation({
    mutationFn: () => supportApi.createSpeedafWorkOrder(ticketId ?? 0, {
      waybillCode: trimmedWaybill,
      callerID: trimmedCaller,
      workOrderType: 'WT0103-05',
      description: description.trim(),
    }),
    onSuccess: afterSuccess,
  })
  const addressMutation = useMutation({
    mutationFn: () => supportApi.submitSpeedafAddressUpdate(ticketId ?? 0, {
      waybillCode: trimmedWaybill,
      callerID: trimmedCaller,
      whatsAppPhone: whatsAppPhone.trim(),
    }),
    onSuccess: afterSuccess,
  })
  const cancelPreviewMutation = useMutation({
    mutationFn: () => supportApi.previewSpeedafCancel(ticketId ?? 0, {
      waybillCode: trimmedWaybill,
      callerID: trimmedCaller,
      reasonCode,
    }),
    onSuccess: (data) => setCancelPreview(data),
  })
  const cancelConfirmMutation = useMutation({
    mutationFn: () => supportApi.confirmSpeedafCancel(ticketId ?? 0, {
      waybillCode: trimmedWaybill,
      callerID: trimmedCaller,
      reasonCode,
      confirmToken: cancelPreview?.confirmToken || '',
    }),
    onSuccess: afterSuccess,
  })

  const clearActionResults = () => {
    setCancelPreview(null)
    waybillLookupMutation.reset()
    workOrderMutation.reset()
    addressMutation.reset()
    cancelPreviewMutation.reset()
    cancelConfirmMutation.reset()
  }

  const resetActionState = (nextAction: SpeedafActionKind) => {
    setAction(nextAction)
    clearActionResults()
  }

  const busy = waybillLookupMutation.isPending || workOrderMutation.isPending || addressMutation.isPending || cancelPreviewMutation.isPending || cancelConfirmMutation.isPending
  const baseReady = action === 'waybill_lookup'
    ? Boolean(ticketId && trimmedCaller && trimmedCountry)
    : Boolean(ticketId && trimmedWaybill && trimmedCaller)
  const submitDisabled = busy || !baseReady
    || (action === 'work_order' && !description.trim())
    || (action === 'address_update' && !whatsAppPhone.trim())
  const actionError = waybillLookupMutation.error || workOrderMutation.error || addressMutation.error || cancelPreviewMutation.error || cancelConfirmMutation.error
  const actionResult = workOrderMutation.data || addressMutation.data || cancelConfirmMutation.data
  const lookupResult = waybillLookupMutation.data

  return (
    <section className="support-panel" aria-label="Speedaf 受控动作">
      <div className="support-panel-head">
        <span>Speedaf 动作</span>
        <Badge tone="warning">需确认</Badge>
      </div>
      <div className="support-action-form">
        <Field label="动作">
          <Select name="speedaf-action" value={action} onChange={(event) => resetActionState(event.target.value as SpeedafActionKind)}>
            <option value="waybill_lookup">电话查单</option>
            <option value="work_order">催派工单</option>
            <option value="address_update">联系号码更新</option>
            <option value="cancel">取消预检</option>
          </Select>
        </Field>
        <div className="support-action-grid">
          <Field label="运单">
            <Input name="speedaf-waybill-code" value={waybillCode} onChange={(event) => { setWaybillCode(event.target.value); clearActionResults() }} placeholder={`${trackingHint}…`} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Caller ID">
            <Input name="speedaf-caller-id" type="tel" inputMode="tel" value={callerID} onChange={(event) => { setCallerID(event.target.value); clearActionResults() }} placeholder="客户电话…" autoComplete="off" spellCheck={false} />
          </Field>
        </div>
        {action === 'waybill_lookup' ? (
          <Field label="国家码">
            <Input name="speedaf-country-code" value={countryCode} onChange={(event) => { setCountryCode(event.target.value.toUpperCase()); clearActionResults() }} placeholder="CH…" autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        {action === 'work_order' ? (
          <Field label="说明">
            <Textarea name="speedaf-work-order-description" value={description} onChange={(event) => setDescription(event.target.value)} rows={3} placeholder="客户诉求摘要…" autoComplete="off" />
          </Field>
        ) : null}
        {action === 'address_update' ? (
          <Field label="WhatsApp 电话">
            <Input name="speedaf-whatsapp-phone" type="tel" inputMode="tel" value={whatsAppPhone} onChange={(event) => { setWhatsAppPhone(event.target.value); clearActionResults() }} placeholder="确认后的联系号码…" autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        {action === 'cancel' ? (
          <Field label="取消原因">
            <Select name="speedaf-cancel-reason" value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); clearActionResults() }}>
              {cancelReasons.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </Select>
          </Field>
        ) : null}
        {actionError ? <ErrorSummary title="Speedaf 动作失败" errors={[errorCopy(actionError, '请稍后重试')]} /> : null}
        {actionResult ? (
          <div className="support-action-result success">
            <strong>{sanitizeDisplayText(actionResult.message || actionResult.status)}</strong>
            {actionResult.jobId ? <small>Job #{actionResult.jobId}</small> : null}
          </div>
        ) : null}
        {action === 'waybill_lookup' && lookupResult ? (
          <div className={`support-action-result ${lookupResult.ok ? 'success' : 'warning'}`}>
            <strong>{lookupResult.ok ? `${lookupResult.candidates.length} 个候选运单` : sanitizeDisplayText(lookupResult.message || lookupResult.failureReason || '查单失败')}</strong>
            {lookupResult.candidates.length ? (
              <div className="support-candidate-list">
                {lookupResult.candidates.slice(0, 5).map((item) => (
                  <div className="support-candidate-row" key={item.waybillCode}>
                    <span>{sanitizeDisplayText(item.waybillCode)}</span>
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setWaybillCode(item.waybillCode)
                        setAction('work_order')
                      }}
                    >
                      填入
                    </Button>
                  </div>
                ))}
              </div>
            ) : <small>{sanitizeDisplayText(lookupResult.message || '没有候选运单')}</small>}
          </div>
        ) : null}
        {cancelPreview ? (
          <div className={`support-action-result ${cancelPreview.cancelAllowed ? 'success' : 'warning'}`}>
            <strong>{cancelPreview.cancelAllowed ? '可取消' : '不可取消'}</strong>
            <small>{sanitizeDisplayText(cancelPreview.currentStatusLabel || cancelPreview.reasonLabel || '')}</small>
          </div>
        ) : null}
        <div className="support-composer-actions">
          {action === 'cancel' ? (
            <>
              <Button disabled={busy || !baseReady} onClick={() => cancelPreviewMutation.mutate()}>{cancelPreviewMutation.isPending ? '预检中…' : '预检'}</Button>
              <Button variant="danger" disabled={busy || !cancelPreview?.cancelAllowed || !cancelPreview.confirmToken} onClick={() => cancelConfirmMutation.mutate()}>
                {cancelConfirmMutation.isPending ? '提交中…' : '确认取消'}
              </Button>
            </>
          ) : action === 'waybill_lookup' ? (
            <Button
              variant="secondary"
              disabled={submitDisabled}
              onClick={() => waybillLookupMutation.mutate()}
            >
              {waybillLookupMutation.isPending ? '查询中…' : '查询运单'}
            </Button>
          ) : (
            <Button
              variant={action === 'work_order' ? 'primary' : 'secondary'}
              disabled={submitDisabled}
              onClick={() => (action === 'work_order' ? workOrderMutation.mutate() : addressMutation.mutate())}
            >
              {busy ? '提交中…' : action === 'work_order' ? '创建工单' : '提交更新'}
            </Button>
          )}
        </div>
      </div>
    </section>
  )
}

function OverviewPanel({
  activeConversation,
  supportMemory,
  onDone,
}: {
  activeConversation?: SupportConversation
  supportMemory?: SupportMemoryLedger | null
  onDone: () => Promise<void>
}) {
  return (
    <aside className="support-context" aria-label="会话上下文">
      <section className="support-panel">
        <div className="support-panel-head">
          <span>会话状态</span>
          {activeConversation ? <Badge tone={toneForConversation(activeConversation)}>{stateLabel(activeConversation)}</Badge> : null}
        </div>
        {activeConversation ? (
          <div className="support-side-stack">
            <div className="support-fact-grid">
              <div>
                <span>渠道</span>
                <strong>{activeConversation.channel === 'whatsapp' ? 'WhatsApp' : 'WebChat'}</strong>
              </div>
              <div>
                <span>AI</span>
                <strong>{activeConversation.ai_suspended ? '已暂停' : activeConversation.ai_status || '运行中'}</strong>
                {activeConversation.ai_pending ? <small>{compactLatency(activeConversation.ai_status_elapsed_ms)}</small> : null}
              </div>
              <div>
                <span>人工</span>
                <strong>{activeConversation.handoff_status || 'none'}</strong>
              </div>
              <div>
                <span>运单</span>
                <strong>{activeConversation.tracking_number_present ? '已提供' : '未提供'}</strong>
              </div>
            </div>
            {activeConversation.required_action ? (
              <div className="support-side-note">
                <span>待处理</span>
                <strong>{sanitizeDisplayText(activeConversation.required_action)}</strong>
              </div>
            ) : null}
          </div>
        ) : (
          <EmptyState title="未选择会话" description="选择一个客户会话后显示状态、证据和下一步。" />
        )}
      </section>
      <section className="support-panel">
        <div className="support-panel-head">
          <span>知识与证据</span>
        </div>
        <SupportMemoryPanel ledger={supportMemory} />
      </section>
      <SpeedafControlledActionsPanel key={activeConversation?.session_key || 'no-conversation'} activeConversation={activeConversation} supportMemory={supportMemory} onDone={onDone} />
    </aside>
  )
}

function KnowledgeView() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const [statusFilter, setStatusFilter] = useState<KnowledgeStatusFilter>('active')
  const [kindFilter, setKindFilter] = useState<KnowledgeKindFilter>('all')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [draft, setDraft] = useState<KnowledgeDraft>(() => knowledgeDraftFromItem())
  const [savedMessage, setSavedMessage] = useState('')
  const [retrievalQuery, setRetrievalQuery] = useState('')
  const editorRef = useRef<HTMLDivElement | null>(null)

  const studio = useQuery({
    queryKey: ['supportWorkbenchKnowledge'],
    queryFn: supportApi.knowledgeStudio,
    refetchInterval: 30000,
    retry: false,
  })
  const items = useQuery({
    queryKey: ['supportWorkbenchKnowledgeItems', deferredSearch, statusFilter, kindFilter],
    queryFn: () => supportApi.knowledgeItems({
      q: deferredSearch,
      status: statusFilter === 'all' ? undefined : statusFilter,
      knowledge_kind: kindFilter === 'all' ? undefined : kindFilter,
    }),
    refetchInterval: 30000,
    retry: false,
  })
  const selectedItem = useMemo(
    () => (items.data?.items ?? []).find((item) => item.id === selectedId) ?? null,
    [items.data?.items, selectedId],
  )

  useEffect(() => {
    if (!isCreating && selectedItem) setDraft(knowledgeDraftFromItem(selectedItem))
  }, [isCreating, selectedItem])

  useEffect(() => {
    if (isCreating || selectedId !== null) return
    const firstItem = items.data?.items?.[0]
    if (firstItem) setSelectedId(firstItem.id)
  }, [isCreating, items.data?.items, selectedId])

  const resetForNew = () => {
    setSelectedId(null)
    setIsCreating(true)
    setDraft(knowledgeDraftFromItem())
    setSavedMessage('')
    window.setTimeout(() => {
      if (window.matchMedia('(max-width: 980px)').matches) {
        editorRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
      }
    }, 0)
  }

  const invalidateKnowledge = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['supportWorkbenchKnowledge'] }),
      queryClient.invalidateQueries({ queryKey: ['supportWorkbenchKnowledgeItems'] }),
    ])
  }

  const saveMutation = useMutation({
    mutationFn: async (publish: boolean) => {
      const payload = knowledgePayloadFromDraft(draft)
      if (!payload.title) throw new Error('请填写知识标题')
      if (!payload.fact_question && !payload.fact_answer && !payload.draft_body) throw new Error('请填写客户问题或答案')
      let item: KnowledgeItem
      if (selectedId && !isCreating) {
        item = await supportApi.updateKnowledgeItem(selectedId, payload)
      } else {
        const itemKey = normalizeKnowledgeKey(draft.item_key) || createKnowledgeKey()
        item = await supportApi.createKnowledgeItem({ ...payload, item_key: itemKey })
      }
      if (publish) {
        await supportApi.publishKnowledgeItem(item.id, 'support console publish')
        item = await supportApi.updateKnowledgeItem(item.id, { status: 'active', fact_status: 'approved' })
      }
      return item
    },
    onSuccess: async (item, publish) => {
      setSelectedId(item.id)
      setIsCreating(false)
      setSavedMessage(publish ? '已保存并上线，AI Runtime 会在同步后用于回答。' : '草稿已保存。')
      await invalidateKnowledge()
    },
  })

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请先选择一条知识')
      await supportApi.publishKnowledgeItem(selectedId, 'support console publish')
      return await supportApi.updateKnowledgeItem(selectedId, { status: 'active', fact_status: 'approved' })
    },
    onSuccess: async (item) => {
      setDraft(knowledgeDraftFromItem(item))
      setSavedMessage('已上线。')
      await invalidateKnowledge()
    },
  })

  const retrievalMutation = useMutation({
    mutationFn: () => supportApi.testKnowledgeRetrieval({
      q: retrievalQuery.trim(),
      channel: draft.channel === 'all' ? null : draft.channel,
      audience_scope: draft.audience_scope || 'customer',
      language: draft.language.trim() || null,
      limit: 5,
    }),
  })

  const busy = saveMutation.isPending || publishMutation.isPending
  const saveError = saveMutation.error || publishMutation.error
  const retrievalHits = retrievalMutation.data?.hits ?? []

  return (
    <section className="support-knowledge-workbench" aria-label="知识库维护">
      <div className="support-panel support-knowledge-list">
        <div className="support-panel-head">
          <span>知识库维护</span>
          {items.isFetching ? <Badge>刷新中</Badge> : null}
        </div>
        <div className="support-action-form">
          <Field label="搜索知识">
            <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入标题、客户问法、答案关键字…" autoComplete="off" />
          </Field>
          <div className="support-segments compact">
            {[
              { value: 'active', label: '已上线' },
              { value: 'draft', label: '草稿' },
              { value: 'all', label: '全部' },
            ].map((item) => (
              <button
                type="button"
                key={item.value}
                className={statusFilter === item.value ? 'active' : ''}
                onClick={() => setStatusFilter(item.value as KnowledgeStatusFilter)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="support-segments compact support-knowledge-kind-filter" aria-label="知识分类筛选">
            {knowledgeKindOptions.map((item) => (
              <button
                type="button"
                key={item.value}
                className={kindFilter === item.value ? 'active' : ''}
                onClick={() => setKindFilter(item.value)}
                title={item.description}
              >
                {item.label}
              </button>
            ))}
          </div>
          <Button variant="primary" onClick={resetForNew}>新建知识</Button>
        </div>
        {items.isError ? (
          <ErrorSummary title="知识库不可用" errors={[errorCopy(items.error, '请稍后重试')]} />
        ) : (
          <div className="support-knowledge-items">
            {(items.data?.items ?? []).map((item) => (
              <button
                type="button"
                className={`support-knowledge-item${selectedId === item.id ? ' active' : ''}`}
                key={item.id}
                onClick={() => {
                  setSelectedId(item.id)
                  setIsCreating(false)
                  setSavedMessage('')
                  window.setTimeout(() => {
                    if (window.matchMedia('(max-width: 980px)').matches) {
                      editorRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
                    }
                  }, 0)
                }}
              >
                <span>
                  <strong>{sanitizeDisplayText(item.title)}</strong>
                  <small>{sanitizeDisplayText(item.fact_question || item.summary || item.item_key)}</small>
                </span>
                <span>
                  <Badge tone={toneForHealth(item.status)}>{knowledgeStatusLabel(item.status)}</Badge>
                  <small>{knowledgeKindLabel(item.knowledge_kind)} · 优先级 {item.priority ?? 100} · v{item.published_version || 0}</small>
                </span>
              </button>
            ))}
            {!items.data?.items?.length ? (
              <EmptyState title="没有找到知识" description="可以调整搜索条件，或新建一条客服知识。" />
            ) : null}
          </div>
        )}
      </div>
      <div className="support-panel support-knowledge-editor" ref={editorRef}>
        <div className="support-panel-head">
          <span>{selectedId && !isCreating ? '编辑知识' : '新建知识'}</span>
          <Badge tone={toneForHealth(draft.status)}>{knowledgeStatusLabel(draft.status)}</Badge>
        </div>
        {saveError ? <ErrorSummary title="保存失败" errors={[errorCopy(saveError, '请稍后重试')]} /> : null}
        {savedMessage ? <div className="support-action-result success"><strong>{savedMessage}</strong></div> : null}
        <div className="support-knowledge-form">
          <Field label="知识标题" required>
            <Input value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} placeholder="例如：末派失败怎么处理" autoComplete="off" />
          </Field>
          {isCreating || !selectedId ? (
            <Field label="内部编号" hint="系统内部使用，保存后不需要客服再关注。">
              <Input value={draft.item_key} onChange={(event) => setDraft((current) => ({ ...current, item_key: normalizeKnowledgeKey(event.target.value) }))} autoComplete="off" spellCheck={false} />
            </Field>
          ) : null}
          <Field label="客户会怎么问" required description="写客户可能发来的原话或问题。">
            <Textarea value={draft.fact_question} onChange={(event) => setDraft((current) => ({ ...current, fact_question: event.target.value }))} rows={4} placeholder="例如：我的包裹显示末派失败怎么办？" autoComplete="off" />
          </Field>
          <Field label="AI 应该知道的答案" required description="这里写事实、规则和处理步骤，不写固定话术。AI Runtime 会结合客户语言自己组织回复。">
            <Textarea value={draft.fact_answer} onChange={(event) => setDraft((current) => ({ ...current, fact_answer: event.target.value }))} rows={8} placeholder="例如：先确认运单号和收件电话；若状态为末派失败，引导客户确认地址和联系方式；必要时人工接管。" autoComplete="off" />
          </Field>
          <Field label="同义问法" hint="一行一个，帮助 AI 命中这条知识。">
            <Textarea value={draft.fact_aliases} onChange={(event) => setDraft((current) => ({ ...current, fact_aliases: event.target.value }))} rows={4} placeholder={'包裹派送失败\n快递没有送到\n最后一公里配送异常'} autoComplete="off" />
          </Field>
          <div className="support-knowledge-grid">
            <Field label="客户可见范围">
              <Select value={draft.audience_scope} onChange={(event) => setDraft((current) => ({ ...current, audience_scope: event.target.value }))}>
                <option value="customer">客户问答</option>
                <option value="internal">内部参考</option>
              </Select>
            </Field>
            <Field label="渠道">
              <Select value={draft.channel} onChange={(event) => setDraft((current) => ({ ...current, channel: event.target.value }))}>
                <option value="all">全部渠道</option>
                <option value="webchat">WebChat</option>
                <option value="whatsapp">WhatsApp</option>
              </Select>
            </Field>
            <Field label="语言">
              <Input value={draft.language} onChange={(event) => setDraft((current) => ({ ...current, language: event.target.value }))} placeholder="空表示自动匹配" autoComplete="off" />
            </Field>
            <Field label="优先级">
              <Input value={draft.priority} type="number" min={0} max={10000} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value }))} />
            </Field>
            <Field label="知识类型">
              <Select value={draft.knowledge_kind} onChange={(event) => setDraft((current) => ({ ...current, knowledge_kind: event.target.value }))}>
                <option value="business_fact">客服问答</option>
                <option value="faq">常见问题</option>
                <option value="policy">规则政策</option>
                <option value="sop">处理流程</option>
                <option value="document">资料文档</option>
              </Select>
            </Field>
            <Field label="回答方式">
              <Select value={draft.answer_mode} onChange={(event) => setDraft((current) => ({ ...current, answer_mode: event.target.value }))}>
                <option value="guided_answer">让 AI 组织语言</option>
                <option value="direct_answer">答案事实优先</option>
              </Select>
            </Field>
          </div>
          <Field label="内部备注">
            <Textarea value={draft.summary} onChange={(event) => setDraft((current) => ({ ...current, summary: event.target.value }))} rows={3} placeholder="给客服自己看的备注，可不填。" autoComplete="off" />
          </Field>
          <div className="support-action-result">
            <strong>{knowledgeKindLabel(draft.knowledge_kind)}：{knowledgeKindDescription(draft.knowledge_kind)}</strong>
            <small>优先级数字越小越靠前。建议：规则政策 10-49，处理流程 50-99，普通客服问答 100，导入资料 200 以上。身份、人设和语言风格属于助手设定，不写成客户知识模板。</small>
          </div>
          <div className="support-composer-actions">
            <Button disabled={busy} onClick={() => saveMutation.mutate(false)}>{saveMutation.isPending ? '保存中…' : '保存草稿'}</Button>
            {selectedId && !isCreating ? (
              <Button disabled={busy} onClick={() => publishMutation.mutate()}>{publishMutation.isPending ? '上线中…' : '上线当前草稿'}</Button>
            ) : null}
            <Button variant="primary" disabled={busy} onClick={() => saveMutation.mutate(true)}>{busy ? '处理中…' : '保存并上线'}</Button>
          </div>
        </div>
      </div>
      <div className="support-panel support-knowledge-test">
        <div className="support-panel-head">
          <span>测试命中</span>
          {retrievalMutation.isPending ? <Badge>测试中</Badge> : null}
        </div>
        <Field label="用一句客户问题测试">
          <Input value={retrievalQuery} onChange={(event) => setRetrievalQuery(event.target.value)} placeholder="例如：包裹末派失败怎么办？" autoComplete="off" />
        </Field>
        <div className="support-composer-actions">
          <Button disabled={!retrievalQuery.trim() || retrievalMutation.isPending} onClick={() => retrievalMutation.mutate()}>测试知识命中</Button>
        </div>
        {retrievalMutation.error ? <ErrorSummary title="测试失败" errors={[errorCopy(retrievalMutation.error, '请稍后重试')]} /> : null}
        {retrievalMutation.data ? (
          <div className="support-side-stack">
            <div className="support-action-result success">
              <strong>{retrievalHits.length ? `命中 ${retrievalHits.length} 条知识` : '没有命中知识'}</strong>
              <small>{retrievalMutation.data.grounding_would_apply ? 'Runtime 可以使用知识上下文' : 'Runtime 可能不会使用知识上下文'}</small>
            </div>
            {retrievalHits.slice(0, 5).map((hit) => (
              <div className="support-side-note" key={`${hit.item_id}-${hit.chunk_index}`}>
                <span>{sanitizeDisplayText(hit.title)}</span>
                <strong>{sanitizeDisplayText(hit.direct_answer || hit.text).slice(0, 260)}</strong>
                <small>score {typeof hit.score === 'number' ? hit.score.toFixed(3) : hit.score}</small>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <div className="support-panel support-knowledge-status">
        <div className="support-panel-head">
          <span>同步状态</span>
          {studio.isFetching ? <Badge>刷新中</Badge> : null}
        </div>
        {studio.isError ? (
          <ErrorSummary title="同步状态不可用" errors={[errorCopy(studio.error, '请稍后重试')]} />
        ) : (
          <div className="support-metrics compact">
            {(studio.data?.kpis ?? []).slice(0, 4).map((item) => (
              <MetricTile key={item.key} label={item.label} value={item.value} tone={item.tone} />
            ))}
            {!studio.data?.kpis?.length ? <MetricTile label="知识条目" value={compactNumber(items.data?.total)} /> : null}
          </div>
        )}
      </div>
    </section>
  )
}

function ChannelsView() {
  const accounts = useQuery({
    queryKey: ['supportWorkbenchChannelAccounts'],
    queryFn: supportApi.channelAccounts,
    refetchInterval: 30000,
    retry: false,
  })
  const activeAccounts = useMemo(
    () => (accounts.data ?? []).filter((item: ChannelAccount) => item.is_active),
    [accounts.data],
  )
  const whatsappAccount = useMemo(
    () => activeAccounts.find((item: ChannelAccount) => item.provider === 'whatsapp'),
    [activeAccounts],
  )
  const whatsappStatus = useQuery({
    queryKey: ['supportWorkbenchWhatsappStatus', whatsappAccount?.account_id],
    queryFn: () => supportApi.whatsappNativeStatus(whatsappAccount?.account_id || ''),
    enabled: Boolean(whatsappAccount?.account_id),
    refetchInterval: 10000,
    retry: false,
  })

  return (
    <section className="support-overview-grid" aria-label="渠道">
      <div className="support-panel wide">
        <div className="support-panel-head">
          <span>渠道账号</span>
          {accounts.isFetching ? <Badge>刷新中</Badge> : null}
        </div>
        {accounts.isError ? (
          <ErrorSummary title="渠道账号不可用" errors={[errorCopy(accounts.error, '请稍后重试')]} />
        ) : (
          <div className="support-table">
            <div className="support-table-row head">
              <span>渠道</span>
              <span>账号</span>
              <span>状态</span>
              <span>优先级</span>
            </div>
            {activeAccounts.slice(0, 12).map((item: ChannelAccount) => (
              <div className="support-table-row" key={item.id}>
                <span data-label="渠道">{item.provider}</span>
                <span data-label="账号">{sanitizeDisplayText(item.display_name || item.account_id)}</span>
                <span data-label="状态"><Badge tone={toneForHealth(item.health_status)}>{item.health_status}</Badge></span>
                <span data-label="优先级">{item.priority}</span>
              </div>
            ))}
            {!activeAccounts.length ? <EmptyState title="暂无渠道账号" description="当前没有可展示的发送线路。" /> : null}
          </div>
        )}
      </div>
      <div className="support-panel">
        <div className="support-panel-head">
          <span>WhatsApp Native</span>
          <Badge tone={toneForHealth(whatsappStatus.data?.status || whatsappAccount?.health_status)}>
            {whatsappStatus.data?.status || whatsappAccount?.health_status || 'unknown'}
          </Badge>
        </div>
        {whatsappStatus.isError ? (
          <ErrorSummary title="WhatsApp 状态不可用" errors={[errorCopy(whatsappStatus.error, '请稍后重试')]} />
        ) : (
          <div className="support-side-stack">
            <div className="support-fact-grid">
              <div>
                <span>账号</span>
                <strong>{sanitizeDisplayText(whatsappAccount?.account_id || '未配置')}</strong>
              </div>
              <div>
                <span>QR</span>
                <strong>{whatsappStatus.data?.qr_status || 'unknown'}</strong>
              </div>
              <div>
                <span>号码</span>
                <strong>{sanitizeDisplayText(whatsappStatus.data?.phone_number || '未返回')}</strong>
              </div>
              <div>
                <span>重连</span>
                <strong>{whatsappStatus.data?.reconnect_count ?? 0}</strong>
              </div>
            </div>
            {whatsappStatus.data?.last_error_message ? (
              <div className="support-side-note danger">
                <span>最近错误</span>
                <strong>{sanitizeDisplayText(whatsappStatus.data.last_error_message)}</strong>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </section>
  )
}

function RuntimeView() {
  const runtime = useQuery({
    queryKey: ['supportWorkbenchProviderRuntimeStatus'],
    queryFn: supportApi.providerRuntimeStatus,
    refetchInterval: 15000,
    retry: false,
  })
  const metrics = useQuery({
    queryKey: ['supportWorkbenchConversationMetrics'],
    queryFn: () => supportApi.supportConversationMetrics(24),
    refetchInterval: 15000,
    retry: false,
  })
  const privateRuntime = runtime.data?.providers?.find((item) => item.name === 'private_ai_runtime')
  const runtimeDiagnostics = privateRuntime?.diagnostics ?? {}
  const latency = metrics.data?.runtime_latency

  return (
    <section className="support-overview-grid" aria-label="运行">
      <div className="support-panel wide">
        <div className="support-panel-head">
          <span>AI Runtime</span>
          <Badge tone={(runtime.data?.warnings?.length ?? 0) ? 'warning' : 'success'}>
            {(runtime.data?.warnings?.length ?? 0) ? '需要关注' : '正常'}
          </Badge>
        </div>
        {runtime.isError ? (
          <ErrorSummary title="运行状态不可用" errors={[errorCopy(runtime.error, '请稍后重试')]} />
        ) : (
          <>
            <div className="support-metrics">
              <MetricTile label="状态" value={runtime.data?.status || 'unknown'} tone={runtime.data?.ok ? 'success' : 'warning'} />
              <MetricTile label="Direct" value={runtimeDiagnostics.direct_model || '未配置'} />
              <MetricTile label="RAG" value={runtimeDiagnostics.rag_model || '未配置'} />
              <MetricTile label="Fallback" value={runtime.data?.fallback_provider || '无'} tone={runtime.data?.fallback_provider ? 'warning' : 'success'} />
            </div>
            <div className="support-fact-grid runtime">
              <div>
                <span>模式</span>
                <strong>{sanitizeDisplayText(runtimeDiagnostics.chat_mode || 'unknown')}</strong>
              </div>
              <div>
                <span>请求形态</span>
                <strong>{sanitizeDisplayText(runtimeDiagnostics.request_shape || 'unknown')}</strong>
              </div>
              <div>
                <span>RAG 隔离</span>
                <strong>{runtimeDiagnostics.rag_runtime_isolated ? '独立 Runtime' : runtimeDiagnostics.chat_mode === 'direct' ? 'Direct 未启用' : '未隔离'}</strong>
              </div>
              <div>
                <span>共享重模型</span>
                <strong>{runtimeDiagnostics.allow_shared_rag_model ? '允许' : '禁止'}</strong>
              </div>
            </div>
            {runtime.data?.warnings?.length ? (
              <div className="support-warning-list">
                {runtime.data.warnings.map((item) => <div key={item}>{sanitizeDisplayText(item)}</div>)}
              </div>
            ) : null}
          </>
        )}
      </div>
      <div className="support-panel">
        <div className="support-panel-head">
          <span>24 小时会话</span>
          {metrics.isFetching ? <Badge>刷新中</Badge> : null}
        </div>
        {metrics.isError ? (
          <ErrorSummary title="会话指标不可用" errors={[errorCopy(metrics.error, '请稍后重试')]} />
        ) : (
          <div className="support-side-stack">
            <div className="support-fact-grid">
              <div>
                <span>总量</span>
                <strong>{metrics.data?.total ?? 0}</strong>
              </div>
              <div>
                <span>待人工</span>
                <strong>{metrics.data?.needs_human ?? 0}</strong>
              </div>
              <div>
                <span>AI 中</span>
                <strong>{metrics.data?.ai_active ?? 0}</strong>
              </div>
              <div>
                <span>WhatsApp</span>
                <strong>{metrics.data?.by_channel?.whatsapp ?? 0}</strong>
              </div>
            </div>
            {latency ? (
              <div className="support-side-note">
                <span>AI 延迟</span>
                <div className="support-fact-grid runtime">
                  <div>
                    <span>样本</span>
                    <strong>{latency.sample_count}</strong>
                  </div>
                  <div>
                    <span>端到端 p50/p90</span>
                    <strong>{compactLatency(latency.total_turn.p50_ms)} / {compactLatency(latency.total_turn.p90_ms)}</strong>
                  </div>
                  <div>
                    <span>Runtime p50/p90</span>
                    <strong>{compactLatency(latency.runtime_total.p50_ms)} / {compactLatency(latency.runtime_total.p90_ms)}</strong>
                  </div>
                  <div>
                    <span>生成 p50/p90</span>
                    <strong>{compactLatency(latency.runtime_eval.p50_ms)} / {compactLatency(latency.runtime_eval.p90_ms)}</strong>
                  </div>
                  <div>
                    <span>冷加载</span>
                    <strong>{latency.cold_load_count}</strong>
                  </div>
                  <div>
                    <span>慢 prompt</span>
                    <strong>{latency.slow_prompt_eval_count}</strong>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </section>
  )
}

export function SupportConsolePage() {
  const client = useQueryClient()
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const initialSearch = useMemo(() => readSupportWorkbenchSearch(), [])
  const [activeView, setActiveView] = useState<WorkbenchView>(initialSearch.activeView)
  const [view, setView] = useState<InboxView>(initialSearch.view)
  const [channel, setChannel] = useState<ChannelFilter>(initialSearch.channel)
  const [query, setQuery] = useState(initialSearch.query)
  const deferredQuery = useDeferredValue(query)
  const [selectedSessionKey, setSelectedSessionKey] = useState<string | null>(initialSearch.sessionKey)
  const [mobileThreadOpen, setMobileThreadOpen] = useState(Boolean(initialSearch.sessionKey))
  const [reply, setReply] = useState('')
  const [confirmReview, setConfirmReview] = useState(false)
  const messagesRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => { document.title = '客服工作台 · Nexus Support' }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    const params = url.searchParams
    if (activeView === 'conversations') params.delete('tab')
    else params.set('tab', activeView)
    if (view === 'open') params.delete('view')
    else params.set('view', view)
    if (channel === 'all') params.delete('channel')
    else params.set('channel', channel)
    if (query.trim()) params.set('q', query.trim())
    else params.delete('q')
    if (selectedSessionKey) params.set('session', selectedSessionKey)
    else params.delete('session')
    const next = `${url.pathname}${params.toString() ? `?${params.toString()}` : ''}${url.hash}`
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(window.history.state, '', next)
    }
  }, [activeView, view, channel, query, selectedSessionKey])

  useEffect(() => {
    if (!reply.trim()) return
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warnBeforeUnload)
    return () => window.removeEventListener('beforeunload', warnBeforeUnload)
  }, [reply])

  const conversations = useQuery({
    queryKey: ['supportConversations', view, channel, deferredQuery],
    queryFn: () => supportApi.supportConversations({ view, channel, q: deferredQuery, limit: 80 }),
    enabled: activeView === 'conversations',
    refetchInterval: activeView === 'conversations' ? 5000 : false,
    staleTime: 1000,
  })
  const state = useQuery({
    queryKey: ['supportConversationState'],
    queryFn: () => supportApi.supportConversationState(),
    refetchInterval: 10000,
  })

  const selected = useMemo(
    () => conversations.data?.items.find((item) => item.session_key === selectedSessionKey) ?? conversations.data?.items[0],
    [conversations.data?.items, selectedSessionKey],
  )
  const detail = useQuery({
    queryKey: ['supportConversationDetail', selected?.session_key],
    queryFn: () => supportApi.supportConversationDetail(selected?.session_key ?? ''),
    enabled: activeView === 'conversations' && Boolean(selected?.session_key),
    refetchInterval: activeView === 'conversations' ? 4000 : false,
    staleTime: 1000,
  })
  const detailReady = Boolean(selected?.session_key && detail.data?.conversation.session_key === selected.session_key)
  const activeConversation = detailReady ? detail.data?.conversation : selected
  const messages = detailReady ? detail.data?.messages ?? [] : []
  const supportMemory = detailReady ? detail.data?.support_memory ?? detail.data?.conversation.support_memory : undefined
  const activeSessionKey = activeConversation?.session_key ?? null

  useEffect(() => {
    if (activeView !== 'conversations') return
    if (!selectedSessionKey && conversations.data?.items[0]) setSelectedSessionKey(conversations.data.items[0].session_key)
    if (selectedSessionKey && conversations.data && !conversations.data.items.some((item) => item.session_key === selectedSessionKey)) {
      setSelectedSessionKey(conversations.data.items[0]?.session_key ?? null)
    }
  }, [activeView, conversations.data, selectedSessionKey])

  useEffect(() => {
    if (activeView !== 'conversations') setMobileThreadOpen(false)
  }, [activeView])

  const refreshActive = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['supportConversations'] }),
      client.invalidateQueries({ queryKey: ['supportConversationDetail'] }),
      client.invalidateQueries({ queryKey: ['supportConversationState'] }),
      client.invalidateQueries({ queryKey: ['supportWorkbenchConversationMetrics'] }),
    ])
  }

  const forceMutation = useMutation({
    mutationFn: () => supportApi.webchatForceTakeover(activeConversation?.ticket_id ?? 0, { reason_code: 'operator_takeover', note: 'Support Console takeover' }),
    onSuccess: refreshActive,
  })
  const acceptMutation = useMutation({
    mutationFn: () => supportApi.webchatAcceptHandoff(activeConversation?.handoff_request_id ?? 0, 'Accepted from Support Console'),
    onSuccess: refreshActive,
  })
  const releaseMutation = useMutation({
    mutationFn: () => supportApi.webchatReleaseHandoff(activeConversation?.handoff_request_id ?? 0, 'Released from Support Console'),
    onSuccess: refreshActive,
  })
  const resumeMutation = useMutation({
    mutationFn: () => supportApi.webchatResumeAi(activeConversation?.handoff_request_id ?? 0, 'Resume AI from Support Console'),
    onSuccess: refreshActive,
  })
  const replyMutation = useMutation({
    mutationFn: (payload: { sessionKey: string; body: string; confirmReview: boolean }) => supportApi.supportConversationReply({
      session_key: payload.sessionKey,
      body: payload.body,
      confirm_review: payload.confirmReview,
    }),
    onSuccess: async () => {
      setReply('')
      setConfirmReview(false)
      await refreshActive()
    },
    onError: (error) => {
      if (parseSafetyError(error)) setConfirmReview(true)
    },
  })
  const resetReplyMutationRef = useRef(replyMutation.reset)

  useEffect(() => {
    resetReplyMutationRef.current = replyMutation.reset
  }, [replyMutation.reset])

  useEffect(() => {
    setReply('')
    setConfirmReview(false)
    resetReplyMutationRef.current()
  }, [activeSessionKey])

  useEffect(() => {
    const node = messagesRef.current
    if (!node || !detailReady) return
    window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight
    })
  }, [activeSessionKey, detailReady, messages.length])

  const safety = parseSafetyError(replyMutation.error)
  const canReply = Boolean(activeConversation?.can_reply)
  const canSend = Boolean(reply.trim() && activeConversation?.session_key && detailReady && canReply && !replyMutation.isPending)
  const showTakeover = Boolean(activeConversation?.can_accept || activeConversation?.can_force_takeover)
  const isBusy = forceMutation.isPending || acceptMutation.isPending || releaseMutation.isPending || resumeMutation.isPending

  const handleTakeover = () => {
    if (activeConversation?.can_accept) acceptMutation.mutate()
    else if (activeConversation?.can_force_takeover) forceMutation.mutate()
  }

  const handleSelectConversation = (sessionKey: string) => {
    setSelectedSessionKey(sessionKey)
    setMobileThreadOpen(true)
  }

  const handleSend = () => {
    if (!activeConversation?.session_key) return
    replyMutation.mutate({ sessionKey: activeConversation.session_key, body: reply.trim(), confirmReview })
  }

  const handleLogout = () => {
    logout()
    navigate({ to: '/login', replace: true })
  }

  const consoleClassName = [
    'support-console',
    activeView === 'conversations' && mobileThreadOpen ? 'mobile-thread-page' : '',
  ].filter(Boolean).join(' ')

  return (
    <main className={consoleClassName} data-testid="nexus-support-console">
      <header className="support-console-head">
        <div>
          <div className="support-eyebrow">Nexus Support</div>
          <h1>客服工作台</h1>
        </div>
        <div className="support-head-status" aria-label="实时状态">
          <Badge tone="default">{state.data?.open ?? 0} 个打开会话</Badge>
          <Badge tone="danger">{state.data?.requested_handoffs ?? 0} 个待人工</Badge>
          <Badge tone="success">{state.data?.my_handoffs ?? 0} 个我的接管</Badge>
          <span className="support-user">{session.data?.display_name || session.data?.username || '客服'}</span>
          <Button variant="ghost" onClick={handleLogout}>退出</Button>
        </div>
      </header>

      <nav className="support-top-tabs" data-testid="support-workbench-tabs" aria-label="客服后台视图">
        {workbenchViews.map((item) => (
          <button
            key={item.value}
            type="button"
            className={activeView === item.value ? 'active' : ''}
            aria-pressed={activeView === item.value}
            onClick={() => setActiveView(item.value)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {activeView === 'conversations' ? (
        <section className={`support-shell ${mobileThreadOpen ? 'mobile-thread-open' : 'mobile-list-open'}`}>
          <aside className="support-sidebar" aria-label="会话队列">
            <div className="support-filter-block">
              <div className="support-segments" aria-label="会话视图">
                {viewOptions.map((item) => (
                  <button type="button" key={item.value} className={view === item.value ? 'active' : ''} aria-pressed={view === item.value} onClick={() => setView(item.value)}>{item.label}</button>
                ))}
              </div>
              <div className="support-segments compact" aria-label="渠道">
                {channelOptions.map((item) => (
                  <button type="button" key={item.value} className={channel === item.value ? 'active' : ''} aria-pressed={channel === item.value} onClick={() => setChannel(item.value)}>{item.label}</button>
                ))}
              </div>
              <Field label="搜索">
                <Input name="support-search" autoComplete="off" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="客户、联系方式…" />
              </Field>
            </div>
            {conversations.isError ? <ErrorSummary title="会话加载失败" errors={[errorCopy(conversations.error, '请稍后重试')]} /> : null}
            <div className="support-list">
              {conversations.isLoading ? <EmptyState text="加载会话中…" /> : null}
              {conversations.data?.items.length === 0 ? <EmptyState title="暂无会话" description="当前筛选条件下没有客户会话。" /> : null}
              {conversations.data?.items.map((item) => (
                <ConversationRow key={item.session_key} item={item} active={item.session_key === activeConversation?.session_key} onSelect={() => handleSelectConversation(item.session_key)} />
              ))}
            </div>
          </aside>

          <section className="support-thread" aria-label="当前会话">
            {activeConversation ? (
              <>
                <div className="support-thread-head">
                  <button type="button" className="support-thread-back" onClick={() => setMobileThreadOpen(false)}>
                    ‹ 会话
                  </button>
                  <div className="support-thread-title">
                    <h2>{sanitizeDisplayText(activeConversation.display_name || activeConversation.customer_contact || '客户')}</h2>
                    <div className="support-thread-subtitle">
                      <Badge tone={toneForChannel(activeConversation.channel)}>{activeConversation.channel === 'whatsapp' ? 'WhatsApp' : 'WebChat'}</Badge>
                      <span>{sanitizeDisplayText(activeConversation.customer_contact || '未提供联系方式')}</span>
                      <span>{stateLabel(activeConversation)}</span>
                    </div>
                  </div>
                  <div className="support-actions">
                    {showTakeover ? <Button disabled={isBusy || !detailReady} onClick={handleTakeover}>人工接管</Button> : null}
                    {activeConversation.can_release ? <Button variant="ghost" disabled={isBusy || !detailReady} onClick={() => releaseMutation.mutate()}>释放</Button> : null}
                    {activeConversation.can_resume_ai ? <Button variant="ghost" disabled={isBusy || !detailReady} onClick={() => resumeMutation.mutate()}>恢复 AI</Button> : null}
                  </div>
                </div>

                {detail.isError ? <ErrorSummary title="会话详情加载失败" errors={[errorCopy(detail.error, '请稍后重试')]} /> : null}

                <div className="support-messages" aria-live="polite" ref={messagesRef}>
                  {!detailReady || detail.isLoading ? <EmptyState text="加载消息中…" /> : null}
                  {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
                </div>

                <div className="support-composer">
                  {!canReply ? <div className="support-inline-note">当前不能直接回复。请先接管，或等待 AI 完成当前处理。</div> : null}
                  {safety ? <ErrorSummary title="回复需要复核" errors={safety.reasons?.length ? safety.reasons : ['安全检查要求人工确认后发送']} /> : null}
                  {replyMutation.isError && !safety ? <ErrorSummary title="发送失败" errors={[errorCopy(replyMutation.error, '请稍后重试')]} /> : null}
                  <Field label="回复客户">
                    <Textarea name="support-reply-body" value={reply} onChange={(event) => { setReply(event.target.value); setConfirmReview(false) }} rows={3} placeholder="输入回复…" />
                  </Field>
                  <div className="support-composer-actions">
                    <Button variant="primary" disabled={!canSend} onClick={handleSend}>{replyMutation.isPending ? '发送中…' : confirmReview ? '确认发送' : '发送'}</Button>
                  </div>
                </div>
              </>
            ) : (
              <EmptyState title="暂无会话" description="当前没有可展示的客户会话。" />
            )}
          </section>

          <OverviewPanel activeConversation={activeConversation} supportMemory={supportMemory} onDone={refreshActive} />
        </section>
      ) : null}

      {activeView === 'knowledge' ? <KnowledgeView /> : null}
      {activeView === 'channels' ? <ChannelsView /> : null}
      {activeView === 'runtime' ? <RuntimeView /> : null}
    </main>
  )
}
