import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import * as Popover from '@radix-ui/react-popover'
import * as Tabs from '@radix-ui/react-tabs'
import * as Tooltip from '@radix-ui/react-tooltip'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AppShell } from '@/layouts/AppShell'
import { ApiError, api } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText, statusTone } from '@/lib/format'
import { findReplyChannelCapability, isCustomerSendableReplyChannel, outboundChannelMissingText } from '@/lib/outboundChannels'
import type { BadgeTone, CaseDetail, Team, WebchatActionAudit, WebchatConversation, WebchatHandoffQueue, WebchatHandoffRequest, WebchatMessage, WebchatThread } from '@/lib/types'
import { useWebchatRealtime, type WebchatRealtimeEvent } from '@/lib/webchatRealtime'
import { AgentWebCallPanel } from '@/components/webcall/AgentWebCallPanel'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Textarea } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'
import { useSession } from '@/hooks/useAuth'
import { canEscalateTickets, canForceWebchatHandoff, canUploadAttachment, canViewWebcallVoiceQueue, canViewWebchatDebug, canWriteInternalNote } from '@/lib/access'

type InboxView = 'requested' | 'mine' | 'ai_active' | 'all' | 'closed'
type FilterKey = 'needs_human' | 'timeout' | 'ai_suspended' | 'unread'
type HandoffQueueView = Exclude<InboxView, 'all'>
type RealtimeHandoffView = 'requested' | 'ai_active' | 'mine'
type ToastState = { message: string; tone?: 'default' | 'danger' | 'success'; action?: { label: string; onClick: () => void } }
type ConfirmState = { title: string; body: string; tone?: 'danger' | 'default'; confirmLabel: string; onConfirm: () => void }
type EscalateState = { open: boolean; teamId: string; note: string }
type ComposerMode = 'reply' | 'internal_note'
type WebchatAISuggestion = {
  id: string
  title: string
  body: string
  source: string
  tone?: BadgeTone
  applyText?: string
}
type SafetyReviewState = {
  body: string
  message: string
  safety: {
    allowed: boolean
    level: string
    reasons: string[]
    requires_human_review: boolean
    normalized_body: string
  }
}
type ReplyMutationInput = { confirmReview?: boolean }

type InboxRow = {
  key: string
  ticketId: number
  ticketNo?: string | null
  title?: string | null
  visitorLabel?: string | null
  status?: string | null
  origin?: string | null
  updatedAt?: string | null
  lastMessage?: string | null
  lastMessageType?: string | null
  needsHuman?: boolean
  aiPending?: boolean
  aiStatus?: string | null
  aiSuspended?: boolean
  handoffStatus?: string | null
  handoffRequestId?: number | null
  activeAgentId?: number | null
  unreadCount?: number
  markedUnread?: boolean
  source: 'handoff' | 'conversation'
  rawHandoff?: WebchatHandoffRequest
  rawConversation?: WebchatConversation
}

const VIEW_LABELS: Record<InboxView, string> = {
  requested: '待接入',
  mine: '我的会话',
  ai_active: 'AI 监控',
  all: '全部',
  closed: '已关闭',
}

const FILTER_LABELS: Record<FilterKey, string> = {
  needs_human: '需人工处理',
  timeout: '超时等待',
  ai_suspended: 'AI 已暂停',
  unread: '未读',
}

const QUICK_REPLIES = [
  '您好，我来帮您确认一下订单的最新信息，请稍等。',
  '我们已收到您的请求，会先核实系统记录再回复您。',
  '请提供运单号或订单号，方便我们继续查询。',
  '该事项需要人工核实，我会继续为您跟进。',
]
const EMOJIS = ['🙂', '👍', '🙏', '✅', '📦', '🚚', '⏳', '📍']
const AI_ACTIVE_STATUSES = new Set(['queued', 'processing', 'bridge_calling', 'fallback_generating'])
const TERMINAL_TICKET_STATUSES = new Set(['closed', 'resolved', 'canceled', 'cancelled'])
const SENSITIVE_AUDIT_KEY = /token|secret|authorization|password|credential|api[_-]?key|visitor[_-]?token|access[_-]?token|participant[_-]?token/i
const SENSITIVE_AUDIT_VALUE = /bearer\s+|visitor[_-]?token|access[_-]?token|participant[_-]?token|secret|authorization|password/i

function backoffMs(failures: number, baseMs: number, maxMs: number) {
  if (failures <= 0) return baseMs
  return Math.min(maxMs, baseMs * 2 ** Math.min(failures, 4))
}

function shortText(value?: string | null, fallback = '-') {
  return sanitizeDisplayText(value || fallback)
}

function aiTone(status?: string | null, pending?: boolean): 'default' | 'warning' | 'success' | 'danger' {
  if (!status) return 'default'
  if (status === 'completed') return 'success'
  if (status === 'failed' || status === 'timeout' || status === 'cancelled') return 'danger'
  if (pending || AI_ACTIVE_STATUSES.has(status)) return 'warning'
  return 'default'
}

function apiErrorText(error: unknown, fallback: string) {
  const safety = safetyFromError(error)
  if (safety) {
    const prefix = safety.level === 'block' ? '安全门阻断' : '安全门复核'
    return `${prefix}：${safety.reasons.length ? safety.reasons.join('；') : safety.normalized_body || fallback}`
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function stringList(value: unknown) {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string')
}

function safetyFromError(error: unknown): SafetyReviewState['safety'] | null {
  if (!(error instanceof ApiError) || !isRecord(error.detail) || !isRecord(error.detail.safety)) return null
  const safety = error.detail.safety
  return {
    allowed: safety.allowed === true,
    level: typeof safety.level === 'string' ? safety.level : 'review',
    reasons: stringList(safety.reasons),
    requires_human_review: safety.requires_human_review === true,
    normalized_body: typeof safety.normalized_body === 'string' ? safety.normalized_body : '',
  }
}

function safetyReviewFromError(error: unknown, body: string): SafetyReviewState | null {
  if (!(error instanceof ApiError) || error.status !== 409 || !isRecord(error.detail)) return null
  const safety = safetyFromError(error)
  if (!safety?.requires_human_review || safety.level === 'block') return null
  return {
    body,
    message: typeof error.detail.message === 'string' ? error.detail.message : '回复需要人工复核后才能发送。',
    safety: { ...safety, normalized_body: safety.normalized_body || body },
  }
}

function redactAuditPayload(value: unknown, depth = 0): unknown {
  if (depth > 3) return '[redacted depth limit]'
  if (typeof value === 'string' && SENSITIVE_AUDIT_VALUE.test(value)) return '[redacted]'
  if (Array.isArray(value)) return value.slice(0, 8).map((item) => redactAuditPayload(item, depth + 1))
  if (!isRecord(value)) return value
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    SENSITIVE_AUDIT_KEY.test(key) ? '[redacted]' : redactAuditPayload(item, depth + 1),
  ]))
}

function payloadSummary(payload: Record<string, unknown>) {
  const redacted = redactAuditPayload(payload)
  if (!isRecord(redacted)) return sanitizeDisplayText(String(redacted || '-'))
  const entries = Object.entries(redacted).filter(([key]) => !SENSITIVE_AUDIT_KEY.test(key)).slice(0, 4)
  if (!entries.length) return '无公开 payload 摘要'
  return entries.map(([key, value]) => {
    const safeValue = typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
      ? String(value)
      : JSON.stringify(value) ?? String(value ?? '')
    return `${sanitizeDisplayText(key)}=${sanitizeDisplayText(safeValue).slice(0, 120)}`
  }).join(' · ')
}

function safeAuditPayloadJson(payload: Record<string, unknown>) {
  return sanitizeDisplayText(JSON.stringify(redactAuditPayload(payload), null, 2))
}

function voiceEvidenceValue(payload: Record<string, unknown> | null | undefined, key: string) {
  const value = payload?.[key]
  if (value === null || value === undefined || value === '') return '-'
  return sanitizeDisplayText(String(value))
}

function latestMessage(thread: WebchatThread | undefined, direction?: string) {
  const messages = thread?.messages ?? []
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const item = messages[index]
    if (!direction || item.direction === direction) return item
  }
  return undefined
}

function latestAiTurn(thread?: WebchatThread) {
  const turns = thread?.ai_turns ?? []
  return turns.length ? turns[turns.length - 1] : undefined
}

function profileName(row: InboxRow, thread?: WebchatThread, detail?: CaseDetail) {
  return detail?.customer?.name || detail?.customer_name || thread?.visitor?.name || row.visitorLabel || 'Anonymous visitor'
}

function buildSuggestedReply(detail: CaseDetail | undefined, latestVisitor?: WebchatMessage) {
  const tracking = detail?.tracking_number ? `运单 ${detail.tracking_number}` : '这个问题'
  const ask = latestVisitor?.body_text || latestVisitor?.body || detail?.last_customer_message || detail?.customer_request
  return ask
    ? `您好，我已看到您提到的情况。我会先核实${tracking}的最新记录，再给您明确答复。`
    : `您好，我会先核实${tracking}的最新记录，再给您明确答复。`
}

function buildWebchatAISuggestions({
  row,
  thread,
  caseDetail,
  handoff,
  webchatReplyEnabled,
  sendDisabledReason,
}: {
  row: InboxRow
  thread?: WebchatThread
  caseDetail?: CaseDetail
  handoff?: WebchatHandoffRequest | null
  webchatReplyEnabled: boolean
  sendDisabledReason: string | null
}) {
  const suggestions: WebchatAISuggestion[] = []
  const requiredAction = thread?.required_action || caseDetail?.required_action || handoff?.recommended_agent_action || row.rawHandoff?.recommended_agent_action
  if (requiredAction) {
    suggestions.push({
      id: 'required-action',
      title: 'Required action',
      body: requiredAction,
      source: thread?.required_action ? 'webchatThread.required_action' : caseDetail?.required_action ? 'caseDetail.required_action' : 'handoff.recommended_agent_action',
      tone: 'warning',
    })
  }

  const latestVisitor = latestMessage(thread, 'visitor')
  if (latestVisitor || caseDetail?.last_customer_message || caseDetail?.customer_request) {
    suggestions.push({
      id: 'customer-intent',
      title: '客户意图',
      body: latestVisitor?.body_text || latestVisitor?.body || caseDetail?.last_customer_message || caseDetail?.customer_request || '客户最近消息已同步到线程。',
      source: latestVisitor ? 'webchatThread.messages' : 'caseDetail.customer_request',
      applyText: buildSuggestedReply(caseDetail, latestVisitor),
    })
  }

  if (caseDetail?.ai_summary || caseDetail?.ai_classification) {
    const confidence = typeof caseDetail.ai_confidence === 'number' ? ` · confidence ${Math.round(caseDetail.ai_confidence * 100)}%` : ''
    suggestions.push({
      id: 'case-ai-summary',
      title: 'AI case summary',
      body: `${caseDetail.ai_summary || caseDetail.ai_classification}${confidence}`,
      source: caseDetail.ai_summary ? 'caseDetail.ai_summary' : 'caseDetail.ai_classification',
      tone: 'success',
    })
  }

  const aiTurn = latestAiTurn(thread)
  if (aiTurn || row.aiStatus || thread?.last_ai_reply_source || thread?.last_ai_fallback_reason) {
    const source = aiTurn?.reply_source || thread?.last_ai_reply_source || 'runtime'
    const fallback = aiTurn?.fallback_reason || thread?.last_ai_fallback_reason
    suggestions.push({
      id: 'ai-runtime',
      title: 'AI runtime',
      body: `${aiTurn?.status || row.aiStatus || 'unknown'} · source ${source}${fallback ? ` · fallback ${fallback}` : ''}`,
      source: aiTurn ? 'webchatThread.ai_turns' : 'webchatThread.ai_runtime',
      tone: aiTone(aiTurn?.status || row.aiStatus, row.aiPending),
    })
  }

  const evidence = caseDetail?.evidence_summary
  if (evidence) {
    suggestions.push({
      id: 'evidence-readiness',
      title: '证据就绪度',
      body: `附件 ${evidence.attachments_count} · OpenClaw ${evidence.openclaw_transcript_count} · 公告 ${evidence.active_market_bulletins_count}`,
      source: 'caseDetail.evidence_summary',
      tone: evidence.loaded ? 'success' : 'warning',
    })
  }

  if (!webchatReplyEnabled || sendDisabledReason) {
    suggestions.push({
      id: 'reply-gate',
      title: '回复门禁',
      body: sendDisabledReason || 'WebChat 回复通道当前不可发送。',
      source: 'outboundChannelCapabilities + handoff ownership',
      tone: 'warning',
    })
  }

  if (!suggestions.length) {
    suggestions.push({
      id: 'no-ai-suggestion',
      title: 'AI Suggestions',
      body: '当前线程未返回 required action、AI 摘要或 AI turn；请以消息流和工单证据为准处理。',
      source: 'webchatThread + caseDetail',
    })
  }
  return suggestions.slice(0, 5)
}

function rowFromHandoff(item: WebchatHandoffRequest): InboxRow {
  return {
    key: `handoff-${item.status}-${item.ticket_id}-${item.id ?? item.ai_turn_id ?? item.webchat_conversation_id}`,
    ticketId: item.ticket_id,
    ticketNo: item.ticket_no,
    title: item.title,
    visitorLabel: item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor',
    status: item.status,
    origin: item.origin,
    updatedAt: item.accepted_at || item.requested_at || undefined,
    lastMessage: item.last_message?.body_text || item.reason_text || item.reason_code || item.trigger_type,
    lastMessageType: item.last_message?.message_type || null,
    needsHuman: item.status === 'requested',
    aiPending: item.ai_pending,
    aiStatus: item.ai_status,
    aiSuspended: item.ai_suspended,
    handoffStatus: item.handoff_status || item.status,
    handoffRequestId: item.id,
    activeAgentId: item.active_agent_id,
    unreadCount: item.unread_count,
    markedUnread: item.marked_unread,
    source: 'handoff',
    rawHandoff: item,
  }
}

function rowFromConversation(item: WebchatConversation): InboxRow {
  return {
    key: `conversation-${item.conversation_id}`,
    ticketId: item.ticket_id,
    ticketNo: item.ticket_no,
    title: item.title,
    visitorLabel: item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor',
    status: item.status,
    origin: item.origin,
    updatedAt: item.updated_at,
    lastMessage: item.last_handoff_reason || item.last_message_type || item.page_url || item.origin || null,
    lastMessageType: item.last_message_type || null,
    needsHuman: item.needs_human,
    aiPending: item.ai_pending,
    aiStatus: item.ai_status,
    aiSuspended: item.ai_suspended,
    handoffStatus: item.handoff_status,
    handoffRequestId: item.current_handoff_request_id,
    activeAgentId: item.active_agent_id,
    unreadCount: item.unread_count,
    markedUnread: item.marked_unread,
    source: 'conversation',
    rawConversation: item,
  }
}

function PrimaryStatus({ row }: { row: InboxRow }) {
  if (row.handoffStatus === 'accepted') return <Badge tone="success">已接管</Badge>
  if (row.needsHuman) return <Badge tone="warning">需人工</Badge>
  if (row.aiSuspended) return <Badge tone="warning">AI 暂停</Badge>
  if (row.aiPending || (row.aiStatus && AI_ACTIVE_STATUSES.has(row.aiStatus))) return <Badge tone="warning">AI 处理中</Badge>
  if (row.status && TERMINAL_TICKET_STATUSES.has(String(row.status))) return <Badge>已关闭</Badge>
  return <Badge tone={statusTone(row.status || 'open')}>{shortText(row.status || 'open')}</Badge>
}

function RealtimePill({ connected, status }: { connected: boolean; status: string }) {
  return connected ? <Badge tone="success">WebSocket 实时</Badge> : <Badge tone="warning">轮询兜底 · {sanitizeDisplayText(status)}</Badge>
}

function ConfirmDialog({ state, onClose }: { state: ConfirmState | null; onClose: () => void }) {
  return (
    <Dialog.Root open={Boolean(state)} onOpenChange={(open) => { if (!open) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="v5-dialog-overlay" />
        <Dialog.Content className="v5-dialog-content">
          <Dialog.Title className="v5-dialog-title">{state?.title}</Dialog.Title>
          <Dialog.Description className="v5-dialog-desc">{state?.body}</Dialog.Description>
          <div className="button-row v5-dialog-actions">
            <Dialog.Close asChild><Button variant="secondary">取消</Button></Dialog.Close>
            <Button variant={state?.tone === 'danger' ? 'danger' : 'primary'} onClick={() => { state?.onConfirm(); onClose() }}>{state?.confirmLabel || '确认'}</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function ComposerTools({
  onInsert,
  onAttach,
  attachmentDisabled,
  attachmentBusy,
}: {
  onInsert: (text: string) => void
  onAttach: (file: File) => void
  attachmentDisabled?: boolean
  attachmentBusy?: boolean
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  return (
    <div className="v5-composer-tools">
      <Popover.Root>
        <Popover.Trigger asChild><button type="button" className="v5-icon-button" aria-label="插入表情">表情</button></Popover.Trigger>
        <Popover.Portal>
          <Popover.Content className="v5-popover" sideOffset={8}>
            <div className="v5-emoji-grid">
              {EMOJIS.map((emoji) => <button type="button" key={emoji} onClick={() => onInsert(emoji)}>{emoji}</button>)}
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      <input
        ref={fileInputRef}
        type="file"
        hidden
        onChange={(event) => {
          const file = event.currentTarget.files?.[0]
          event.currentTarget.value = ''
          if (file) onAttach(file)
        }}
      />
      <button
        type="button"
        className="v5-tool-link"
        aria-label="上传工单附件"
        title={attachmentDisabled ? '请选择会话并确认账号具备 attachment.upload' : '上传到工单附件，不作为 WebChat 文本消息发送'}
        disabled={attachmentDisabled || attachmentBusy}
        onClick={() => fileInputRef.current?.click()}
      >
        {attachmentBusy ? '上传中' : '上传证据'}
      </button>
      <Popover.Root>
        <Popover.Trigger asChild><button type="button" className="v5-tool-link">快捷回复</button></Popover.Trigger>
        <Popover.Portal>
          <Popover.Content className="v5-popover v5-template-popover" sideOffset={8}>
            <div className="v5-template-list">
              {QUICK_REPLIES.map((reply) => <button type="button" key={reply} onClick={() => onInsert(reply)}>{reply}</button>)}
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  )
}

function InboxHeader({
  view,
  setView,
  counts,
}: {
  view: InboxView
  setView: (view: InboxView) => void
  counts: Partial<Record<InboxView, number>>
}) {
  return (
    <Tabs.Root value={view} onValueChange={(next) => setView(next as InboxView)}>
      <Tabs.List className="v5-tabs-list" aria-label="WebChat Inbox views">
        {(['requested', 'mine', 'ai_active', 'all', 'closed'] as InboxView[]).map((item) => (
          <Tabs.Trigger className="v5-tab" key={item} value={item}>
            {VIEW_LABELS[item]} <span>{typeof counts[item] === 'number' ? counts[item] : '—'}</span>
          </Tabs.Trigger>
        ))}
      </Tabs.List>
    </Tabs.Root>
  )
}

function UnifiedInbox({
  rows,
  selectedTicketId,
  onSelect,
  incomingVoiceByTicket,
  view,
  setView,
  counts,
  search,
  setSearch,
  activeFilters,
  toggleFilter,
}: {
  rows: InboxRow[]
  selectedTicketId: number | null
  onSelect: (ticketId: number) => void
  incomingVoiceByTicket: Map<number, boolean>
  view: InboxView
  setView: (view: InboxView) => void
  counts: Partial<Record<InboxView, number>>
  search: string
  setSearch: (value: string) => void
  activeFilters: Set<FilterKey>
  toggleFilter: (key: FilterKey) => void
}) {
  return (
    <Card className="v5-panel v5-inbox-panel">
      <CardHeader title="统一 Inbox" subtitle="按处理优先级收敛待接入、我的会话和 AI 监控。" />
      <CardBody>
        <InboxHeader view={view} setView={setView} counts={counts} />
        <div className="v5-search-row">
          <input className="v5-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索客户 / 工单号 / 消息" />
          <Tooltip.Provider delayDuration={150}>
            <Tooltip.Root>
              <Tooltip.Trigger asChild><button type="button" className="v5-icon-button">⌕</button></Tooltip.Trigger>
              <Tooltip.Portal><Tooltip.Content className="v5-tooltip">当前视图会在本页按客户、工单号和最近消息筛选。</Tooltip.Content></Tooltip.Portal>
            </Tooltip.Root>
          </Tooltip.Provider>
        </div>
        <div className="v5-filter-row">
          {(['needs_human', 'timeout', 'ai_suspended', 'unread'] as FilterKey[]).map((key) => (
            <button type="button" key={key} className="v5-filter-chip" data-active={activeFilters.has(key)} onClick={() => toggleFilter(key)}>{FILTER_LABELS[key]}</button>
          ))}
        </div>
        <div className="v5-inbox-list" role="listbox" aria-label="WebChat conversations">
          {rows.map((row) => (
            <button
              type="button"
              key={row.key}
              role="option"
              aria-selected={selectedTicketId === row.ticketId}
              className="v5-inbox-item"
              data-active={selectedTicketId === row.ticketId}
              onClick={() => onSelect(row.ticketId)}
            >
              <div className="v5-inbox-item-main">
                <div className="v5-avatar">{shortText(row.visitorLabel).slice(0, 2).toUpperCase()}</div>
                <div className="v5-inbox-item-copy">
                  <div className="v5-inbox-title"><span>{shortText(row.visitorLabel)}</span><small>{shortText(row.ticketNo || `#${row.ticketId}`)}</small></div>
                  <div className="v5-inbox-preview">{shortText(row.lastMessage || row.title || '暂无最近消息')}</div>
                </div>
              </div>
              <div className="v5-inbox-meta">
                <PrimaryStatus row={row} />
                {incomingVoiceByTicket.get(row.ticketId) ? <Badge tone="warning">Incoming WebCall</Badge> : null}
                {(row.unreadCount || row.markedUnread) ? <Badge tone="warning">未读 {row.unreadCount || 1}</Badge> : null}
                {row.aiStatus ? <Badge tone={aiTone(row.aiStatus, row.aiPending)}>AI {shortText(row.aiStatus)}</Badge> : null}
                {row.updatedAt ? <small>{formatDateTime(row.updatedAt)}</small> : null}
              </div>
            </button>
          ))}
          {!rows.length ? <EmptyState text="当前视图没有会话。" /> : null}
        </div>
      </CardBody>
    </Card>
  )
}

function MessageBubble({ msg, allowDebug }: { msg: WebchatMessage; allowDebug: boolean }) {
  const role = msg.direction === 'visitor' ? 'visitor' : msg.direction === 'system' ? 'system' : msg.direction === 'ai' ? 'ai' : msg.direction === 'action' ? 'action' : 'agent'
  const body = sanitizeDisplayText(msg.body_text || msg.body || '')
  const payload = msg.payload_json && typeof msg.payload_json === 'object' ? msg.payload_json as Record<string, unknown> : null
  const cardActions = Array.isArray(payload?.actions) ? payload.actions as Array<{ id?: string; label?: string }> : []
  if (msg.message_type === 'voice_call') {
    return (
      <article className="v5-message" data-role="agent" data-testid="voice-call-evidence-card">
        <div className="v5-message-head">
          <strong>WebCall evidence · {voiceEvidenceValue(payload, 'status')}</strong>
          <span>{formatDateTime(msg.created_at)}</span>
        </div>
        <p>{body}</p>
        <div className="v5-evidence-grid">
          <div className="v5-kv"><span>voice_session_id</span><strong>{voiceEvidenceValue(payload, 'voice_session_id')}</strong></div>
          <div className="v5-kv"><span>provider</span><strong>{voiceEvidenceValue(payload, 'provider')}</strong></div>
          <div className="v5-kv"><span>accepted_by</span><strong>{voiceEvidenceValue(payload, 'accepted_by')}</strong></div>
          <div className="v5-kv"><span>ended_by</span><strong>{voiceEvidenceValue(payload, 'ended_by')}</strong></div>
          <div className="v5-kv"><span>ringing_duration_seconds</span><strong>{voiceEvidenceValue(payload, 'ringing_duration_seconds')}</strong></div>
          <div className="v5-kv"><span>talk_duration_seconds</span><strong>{voiceEvidenceValue(payload, 'talk_duration_seconds')}</strong></div>
          <div className="v5-kv"><span>total_duration_seconds</span><strong>{voiceEvidenceValue(payload, 'total_duration_seconds')}</strong></div>
          <div className="v5-kv"><span>recording_status</span><strong>{voiceEvidenceValue(payload, 'recording_status')}</strong></div>
          <div className="v5-kv"><span>transcript_status</span><strong>{voiceEvidenceValue(payload, 'transcript_status')}</strong></div>
          <div className="v5-kv"><span>summary_status</span><strong>{voiceEvidenceValue(payload, 'summary_status')}</strong></div>
        </div>
      </article>
    )
  }
  return (
    <article className="v5-message" data-role={role}>
      <div className="v5-message-head">
        <strong>{role === 'visitor' ? '客户' : role === 'ai' ? 'AI 助手' : role === 'system' ? '系统' : role === 'action' ? '客户动作' : sanitizeDisplayText(msg.author_label || '客服')}</strong>
        <span>{formatDateTime(msg.created_at)}</span>
      </div>
      {msg.message_type === 'card' && payload ? (
        <div className="v5-card-message">
          <strong>{sanitizeDisplayText(String(payload.title || body || '结构化卡片'))}</strong>
          {payload.body ? <p>{sanitizeDisplayText(String(payload.body))}</p> : null}
          <div className="badges">
            <Badge>{sanitizeDisplayText(String(payload.card_type || 'card'))}</Badge>
            {cardActions.map((action) => <Badge key={action.id || action.label}>{sanitizeDisplayText(action.label || action.id || 'action')}</Badge>)}
          </div>
        </div>
      ) : <p>{body}</p>}
      {allowDebug && msg.payload_json ? <details className="v5-debug"><summary>payload</summary><pre>{sanitizeDisplayText(JSON.stringify(msg.payload_json, null, 2))}</pre></details> : null}
    </article>
  )
}

function ConversationWorkspace({
  selectedRow,
  thread,
  allowDebug,
  reply,
  setReply,
  composerMode,
  setComposerMode,
  internalNote,
  setInternalNote,
  hasFactEvidence,
  setHasFactEvidence,
  safetyReview,
  canSend,
  sendDisabledReason,
  onSend,
  canSaveInternalNote,
  onSaveInternalNote,
  internalNotePending,
  onConfirmReview,
  onDismissReview,
  sendPending,
  onInsert,
  onAttach,
  canAttach,
  attachmentPending,
  onCopyLink,
  onEscalate,
  canEscalate,
  onMarkReadState,
  readStatePending,
  onOpenContext,
}: {
  selectedRow: InboxRow | null
  thread?: WebchatThread
  allowDebug: boolean
  reply: string
  setReply: (value: string) => void
  composerMode: ComposerMode
  setComposerMode: (value: ComposerMode) => void
  internalNote: string
  setInternalNote: (value: string) => void
  hasFactEvidence: boolean
  setHasFactEvidence: (value: boolean) => void
  safetyReview: SafetyReviewState | null
  canSend: boolean
  sendDisabledReason: string | null
  onSend: () => void
  canSaveInternalNote: boolean
  onSaveInternalNote: () => void
  internalNotePending: boolean
  onConfirmReview: () => void
  onDismissReview: () => void
  sendPending: boolean
  onInsert: (text: string) => void
  onAttach: (file: File) => void
  canAttach: boolean
  attachmentPending: boolean
  onCopyLink: () => void
  onEscalate: () => void
  canEscalate: boolean
  onMarkReadState: (markedUnread: boolean) => void
  readStatePending: boolean
  onOpenContext: () => void
}) {
  if (!selectedRow) return <Card className="v5-panel v5-workspace"><CardBody><EmptyState text="请选择一个 WebChat 会话。" /></CardBody></Card>
  return (
    <Card className="v5-panel v5-workspace">
      <div className="v5-conversation-head">
        <div className="v5-avatar large">{shortText(selectedRow.visitorLabel).slice(0, 2).toUpperCase()}</div>
        <div>
          <h2>{shortText(selectedRow.visitorLabel)}</h2>
          <div className="v5-head-meta">工单 {shortText(selectedRow.ticketNo || `#${selectedRow.ticketId}`)} · WebChat · {shortText(selectedRow.origin || 'unknown origin')}</div>
        </div>
        <div className="v5-head-actions">
          <PrimaryStatus row={selectedRow} />
          <Badge tone={selectedRow.aiSuspended ? 'warning' : aiTone(selectedRow.aiStatus, selectedRow.aiPending)}>{selectedRow.aiSuspended ? 'AI 已暂停' : `AI ${sanitizeDisplayText(selectedRow.aiStatus || 'none')}`}</Badge>
          <Button variant="secondary" className="v5-context-toggle" onClick={onOpenContext}>上下文</Button>
        </div>
      </div>
      <div className="v5-state-banner">当前会话使用真实 WebChat 线程。接管、回复、WebCall 操作均以 API 返回为准。</div>
      <div className="v5-message-list" aria-live="polite">
        {(thread?.messages ?? []).map((msg) => <MessageBubble key={msg.id} msg={msg} allowDebug={allowDebug} />)}
        {thread && !(thread.messages ?? []).length ? <EmptyState text="该会话暂无消息。" /> : null}
      </div>
      <div className="v5-composer" data-testid="webchat-template-reply-note-composer">
        <Tabs.Root value={composerMode} onValueChange={(value) => setComposerMode(value as ComposerMode)}>
          <div className="v5-composer-bar">
            <Tabs.List className="v5-composer-tabs" aria-label="WebChat composer mode">
              <Tabs.Trigger className="v5-composer-tab" value="reply">客户回复</Tabs.Trigger>
              <Tabs.Trigger className="v5-composer-tab" value="internal_note">内部备注</Tabs.Trigger>
            </Tabs.List>
            <span>{composerMode === 'reply' ? reply.length : internalNote.length} / {composerMode === 'reply' ? 2000 : 4000}</span>
          </div>
          <Tabs.Content value="reply" className="v5-composer-pane">
            <Field label="回复内容" disabledReason={sendDisabledReason || undefined}>
              <Textarea value={reply} onChange={(event) => setReply(event.target.value)} placeholder="输入客户可见回复；Ctrl/Cmd + Enter 发送。" />
            </Field>
            <label className="v5-compact-check">
              <input type="checkbox" checked={hasFactEvidence} onChange={(event) => setHasFactEvidence(event.target.checked)} />
              <span>已核对物流事实证据</span>
            </label>
            {safetyReview ? (
              <div className="v5-review-panel" role="alert">
                <div>
                  <strong>回复需要人工复核</strong>
                  <p>{sanitizeDisplayText(safetyReview.message)}</p>
                </div>
                {safetyReview.safety.reasons.length ? (
                  <ul>
                    {safetyReview.safety.reasons.map((reason) => <li key={reason}>{sanitizeDisplayText(reason)}</li>)}
                  </ul>
                ) : null}
                <div className="v5-review-normalized">
                  <span>后端规范化正文</span>
                  <p>{sanitizeDisplayText(safetyReview.safety.normalized_body)}</p>
                </div>
                <div className="button-row">
                  <Button variant="primary" disabled={!canSend || sendPending} onClick={onConfirmReview}>{sendPending ? '发送中…' : '确认已复核并发送'}</Button>
                  <Button variant="secondary" disabled={sendPending} onClick={onDismissReview}>返回修改</Button>
                </div>
              </div>
            ) : null}
            <div className="v5-composer-footer">
              <ComposerTools onInsert={onInsert} onAttach={onAttach} attachmentDisabled={!canAttach} attachmentBusy={attachmentPending} />
              <div className="button-row">
                <DropdownMenu.Root>
                  <DropdownMenu.Trigger asChild><Button variant="secondary">更多操作</Button></DropdownMenu.Trigger>
                  <DropdownMenu.Portal>
                    <DropdownMenu.Content className="v5-menu" sideOffset={8}>
                      <DropdownMenu.Item className="v5-menu-item" onSelect={onCopyLink}>复制会话链接</DropdownMenu.Item>
                      <DropdownMenu.Item className="v5-menu-item" disabled={readStatePending} onSelect={() => onMarkReadState(Boolean(!(selectedRow.unreadCount || selectedRow.markedUnread)))}>
                        {(selectedRow.unreadCount || selectedRow.markedUnread) ? '标记已读' : '标记未读'}
                      </DropdownMenu.Item>
                      <DropdownMenu.Item className="v5-menu-item" disabled={!canEscalate} title={canEscalate ? '升级到指定团队' : '缺少 ticket.escalate'} onSelect={onEscalate}>升级主管</DropdownMenu.Item>
                    </DropdownMenu.Content>
                  </DropdownMenu.Portal>
                </DropdownMenu.Root>
                <Button variant="primary" disabled={!canSend || sendPending} onClick={onSend}>{sendPending ? '发送中…' : '发送 WebChat 回复'}</Button>
              </div>
            </div>
          </Tabs.Content>
          <Tabs.Content value="internal_note" className="v5-composer-pane">
            <Field label="内部备注" disabledReason={canSaveInternalNote ? undefined : '缺少 note.write.internal'}>
              <Textarea value={internalNote} onChange={(event) => setInternalNote(event.target.value)} placeholder="写入仅内部可见的处理备注。" />
            </Field>
            <div className="v5-composer-footer v5-composer-footer-end">
              <Button
                variant="primary"
                disabled={!canSaveInternalNote || !internalNote.trim() || internalNotePending}
                onClick={onSaveInternalNote}
              >
                {internalNotePending ? '保存中…' : '保存内部备注'}
              </Button>
            </div>
          </Tabs.Content>
        </Tabs.Root>
      </div>
    </Card>
  )
}

function Section({ title, children, defaultOpen = true, testId }: { title: string; children: ReactNode; defaultOpen?: boolean; testId?: string }) {
  return <details className="v5-side-section" open={defaultOpen} data-testid={testId}><summary>{title}</summary><div>{children}</div></details>
}

function byteLabel(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 102.4) / 10} KB`
  return `${Math.round(value / 1024 / 102.4) / 10} MB`
}

function CaseEvidencePanel({
  detail,
  isLoading,
  isError,
  onRetry,
}: {
  detail?: CaseDetail
  isLoading: boolean
  isError: boolean
  onRetry: () => void
}) {
  if (isLoading) return <Skeleton lines={4} />
  if (isError) {
    return (
      <div className="v5-error-block">
        <p>系统证据加载失败。</p>
        <Button variant="secondary" onClick={onRetry}>重试</Button>
      </div>
    )
  }
  if (!detail) return <p className="section-subtitle">选择会话后加载工单证据。</p>

  const summary = detail.evidence_summary
  const attachments = detail.attachments ?? []
  const refs = detail.openclaw_attachment_references ?? []
  const bulletins = detail.active_market_bulletins ?? []
  const transcript = detail.openclaw_transcript ?? []
  return (
    <div className="v5-evidence">
      <div className="v5-evidence-metrics">
        <div><strong>{summary?.attachments_count ?? detail.attachments_count ?? attachments.length}</strong><span>附件</span></div>
        <div><strong>{summary?.openclaw_transcript_count ?? detail.openclaw_transcript_count ?? transcript.length}</strong><span>OpenClaw 消息</span></div>
        <div><strong>{summary?.openclaw_attachment_references_count ?? detail.openclaw_attachment_references_count ?? refs.length}</strong><span>远端附件</span></div>
        <div><strong>{summary?.active_market_bulletins_count ?? detail.active_market_bulletins_count ?? bulletins.length}</strong><span>公告口径</span></div>
      </div>
      <div className="v5-evidence-list">
        {attachments.slice(0, 4).map((item) => (
          <div key={item.id}>
            <strong>{item.download_url ? <a href={item.download_url} target="_blank" rel="noreferrer">{sanitizeDisplayText(item.file_name)}</a> : sanitizeDisplayText(item.file_name)}</strong>
            <span>{sanitizeDisplayText(item.mime_type || 'file')} · {byteLabel(item.file_size)} · {sanitizeDisplayText(item.visibility || 'external')}</span>
          </div>
        ))}
        {!attachments.length ? <p className="section-subtitle">暂无工单附件。</p> : null}
      </div>
      {refs.length ? (
        <div className="v5-evidence-list">
          {refs.slice(0, 3).map((item) => (
            <div key={item.id}>
              <strong>{sanitizeDisplayText(item.filename || item.remote_attachment_id)}</strong>
              <span>{sanitizeDisplayText(item.storage_status)} · {sanitizeDisplayText(item.content_type || 'unknown')}</span>
            </div>
          ))}
        </div>
      ) : null}
      {bulletins.length ? (
        <div className="v5-evidence-list">
          {bulletins.slice(0, 2).map((item) => (
            <div key={item.id}>
              <strong>{sanitizeDisplayText(item.title)}</strong>
              <span>{sanitizeDisplayText(item.summary || item.category || item.severity || 'active bulletin')}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function ActionAuditPanel({ actions = [], allowDebug }: { actions?: WebchatActionAudit[]; allowDebug: boolean }) {
  if (!actions.length) return <p className="section-subtitle">暂无客户动作审计。</p>
  return (
    <div className="v5-action-audit-list">
      {actions.slice(-8).map((action) => (
        <div key={action.id} className="v5-action-audit-item">
          <div className="v5-action-audit-head">
            <strong>{sanitizeDisplayText(action.action_type)}</strong>
            <Badge tone={statusTone(action.status)}>{sanitizeDisplayText(action.status)}</Badge>
          </div>
          <div className="v5-action-audit-meta">
            <span>submitted_by: {sanitizeDisplayText(action.submitted_by || '-')}</span>
            <span>created_at: {formatDateTime(action.created_at)}</span>
          </div>
          <p>{payloadSummary(action.payload || {})}</p>
          {allowDebug ? (
            <details className="v5-debug">
              <summary>redacted payload</summary>
              <pre>{safeAuditPayloadJson(action.payload || {})}</pre>
            </details>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function CustomerProfilePanel({ row, thread, detail }: { row: InboxRow; thread?: WebchatThread; detail?: CaseDetail }) {
  const name = profileName(row, thread, detail)
  const contactRows = [
    ['手机', detail?.customer?.phone || thread?.visitor?.phone || row.rawHandoff?.visitor_phone || row.rawConversation?.visitor_phone],
    ['邮箱', detail?.customer?.email || thread?.visitor?.email || row.rawHandoff?.visitor_email || row.rawConversation?.visitor_email],
    ['偏好渠道', detail?.preferred_reply_channel],
    ['联系目标', detail?.preferred_reply_contact],
    ['运单', detail?.tracking_number],
    ['国家', detail?.destination_country || detail?.country_code],
  ].filter(([, value]) => Boolean(value))
  const stats = [
    ['消息', thread?.messages?.length ?? 0],
    ['动作', thread?.actions?.length ?? 0],
    ['事件', thread?.events?.length ?? 0],
    ['AI turns', thread?.ai_turns?.length ?? 0],
  ]
  const recentMessages = (thread?.messages ?? []).filter((message) => message.direction !== 'system').slice(-3)
  return (
    <div className="v5-profile" data-testid="webchat-template-customer-profile">
      <div className="v5-profile-head">
        <div className="v5-avatar">{shortText(name).slice(0, 2).toUpperCase()}</div>
        <div>
          <strong>{shortText(name)}</strong>
          <span>{shortText(detail?.customer_request || detail?.issue_summary || row.title || 'WebChat customer')}</span>
        </div>
      </div>
      <div className="v5-profile-stats">
        {stats.map(([label, value]) => (
          <div key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
      <div className="v5-profile-fields">
        <div className="v5-kv"><span>工单</span><strong>{shortText(row.ticketNo || `#${row.ticketId}`)}</strong></div>
        <div className="v5-kv"><span>来源</span><strong>{shortText(row.origin || thread?.origin || 'unknown')}</strong></div>
        <div className="v5-kv"><span>页面</span><strong>{shortText(thread?.page_url || row.rawConversation?.page_url || '待客户侧上报')}</strong></div>
        {contactRows.map(([label, value]) => <div key={label} className="v5-kv"><span>{label}</span><strong>{shortText(value)}</strong></div>)}
      </div>
      <div className="v5-recent-list" aria-label="Recent Conversations">
        {recentMessages.map((message) => (
          <div key={message.id}>
            <strong>{message.direction === 'visitor' ? '客户' : message.direction === 'ai' ? 'AI 助手' : sanitizeDisplayText(message.author_label || '客服')}</strong>
            <span>{shortText(message.body_text || message.body)}</span>
          </div>
        ))}
        {!recentMessages.length ? <p className="section-subtitle">暂无最近消息。</p> : null}
      </div>
    </div>
  )
}

function AISuggestionsPanel({ suggestions, onApply }: { suggestions: WebchatAISuggestion[]; onApply: (text: string) => void }) {
  return (
    <div className="v5-ai-suggestions" data-testid="webchat-template-ai-suggestions">
      {suggestions.map((suggestion) => (
        <div className="v5-suggestion" key={suggestion.id}>
          <div className="v5-suggestion-head">
            <strong>{sanitizeDisplayText(suggestion.title)}</strong>
            <Badge tone={suggestion.tone || 'default'}>{sanitizeDisplayText(suggestion.source)}</Badge>
          </div>
          <p>{sanitizeDisplayText(suggestion.body)}</p>
          {suggestion.applyText ? (
            <Button variant="secondary" onClick={() => onApply(suggestion.applyText as string)}>插入回复</Button>
          ) : null}
        </div>
      ))}
    </div>
  )
}

function SessionActionsPanel({
  row,
  canEscalate,
  readStatePending,
  onCopyLink,
  onMarkReadState,
  onEscalate,
  onApplySummary,
}: {
  row: InboxRow
  canEscalate: boolean
  readStatePending: boolean
  onCopyLink: () => void
  onMarkReadState: (markedUnread: boolean) => void
  onEscalate: () => void
  onApplySummary: () => void
}) {
  const isUnread = Boolean(row.unreadCount || row.markedUnread)
  return (
    <div className="v5-session-actions" data-testid="webchat-template-session-actions">
      <Button variant="secondary" onClick={onCopyLink}>复制会话链接</Button>
      <Button variant="secondary" disabled={readStatePending} onClick={() => onMarkReadState(!isUnread)}>{isUnread ? '标记已读' : '标记未读'}</Button>
      <Button variant="secondary" disabled={!canEscalate} title={canEscalate ? '升级到指定团队' : '缺少 ticket.escalate'} onClick={onEscalate}>升级主管</Button>
      <Button variant="secondary" onClick={onApplySummary}>插入交接摘要</Button>
    </div>
  )
}

function EscalateDialog({
  state,
  setState,
  teams,
  busy,
  onSubmit,
}: {
  state: EscalateState
  setState: (next: EscalateState) => void
  teams: Team[]
  busy: boolean
  onSubmit: () => void
}) {
  const canSubmit = Boolean(state.teamId && state.note.trim())
  return (
    <Dialog.Root open={state.open} onOpenChange={(open) => setState({ ...state, open })}>
      <Dialog.Portal>
        <Dialog.Overlay className="v5-dialog-overlay" />
        <Dialog.Content className="v5-dialog-content">
          <Dialog.Title className="v5-dialog-title">升级主管处理</Dialog.Title>
          <Dialog.Description className="v5-dialog-desc">选择目标团队并写明升级原因；提交后调用工单升级 API 并写入审计。</Dialog.Description>
          <div className="v5-escalate-form">
            <label>
              <span>目标团队</span>
              <select className="v5-select" value={state.teamId} onChange={(event) => setState({ ...state, teamId: event.target.value })}>
                <option value="">请选择团队</option>
                {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
              </select>
            </label>
            <label>
              <span>升级原因</span>
              <textarea className="textarea" value={state.note} rows={4} onChange={(event) => setState({ ...state, note: event.target.value })} />
            </label>
          </div>
          <div className="button-row v5-dialog-actions">
            <Dialog.Close asChild><Button variant="secondary">取消</Button></Dialog.Close>
            <Button variant="primary" disabled={!canSubmit || busy} onClick={onSubmit}>{busy ? '升级中…' : '确认升级'}</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function ContextPanel({
  row,
  thread,
  caseDetail,
  evidenceLoading,
  evidenceError,
  onRetryEvidence,
  selectedHandoff,
  realtimeLabel,
  allowDebug,
  canForceTakeover,
  onAccept,
  onDecline,
  onForce,
  onRelease,
  onResume,
  onCopyLink,
  onMarkReadState,
  readStatePending,
  onEscalate,
  canEscalate,
  onApplySuggestion,
  webchatReplyEnabled,
  sendDisabledReason,
  busy,
}: {
  row: InboxRow | null
  thread?: WebchatThread
  caseDetail?: CaseDetail
  evidenceLoading: boolean
  evidenceError: boolean
  onRetryEvidence: () => void
  selectedHandoff?: WebchatHandoffRequest | null
  realtimeLabel: ReactNode
  allowDebug: boolean
  canForceTakeover: boolean
  onAccept: (requestId: number) => void
  onDecline: (requestId: number) => void
  onForce: (ticketId: number) => void
  onRelease: (requestId: number) => void
  onResume: (requestId: number) => void
  onCopyLink: () => void
  onMarkReadState: (markedUnread: boolean) => void
  readStatePending: boolean
  onEscalate: () => void
  canEscalate: boolean
  onApplySuggestion: (text: string) => void
  webchatReplyEnabled: boolean
  sendDisabledReason: string | null
  busy: boolean
}) {
  if (!row) return <Card className="v5-panel v5-side-panel"><CardBody><EmptyState text="请选择会话查看上下文。" /></CardBody></Card>
  const handoff = selectedHandoff || row.rawHandoff || null
  const canAccept = handoff?.status === 'requested' && typeof handoff.id === 'number' && handoff.can_accept !== false
  const canRelease = handoff?.status === 'accepted' && handoff.can_release === true && typeof handoff.id === 'number'
  const canResume = handoff?.can_resume_ai === true && typeof handoff.id === 'number'
  const forceVisible = row.aiPending || (row.aiStatus && AI_ACTIVE_STATUSES.has(row.aiStatus))
  const forceAllowed = canForceTakeover && (handoff?.can_force_takeover ?? row.rawHandoff?.can_force_takeover ?? true)
  const suggestions = buildWebchatAISuggestions({ row, thread, caseDetail, handoff, webchatReplyEnabled, sendDisabledReason })
  const latestVisitor = latestMessage(thread, 'visitor')
  const handoffSummary = [
    `客户：${profileName(row, thread, caseDetail)}`,
    `工单：${row.ticketNo || `#${row.ticketId}`}`,
    `状态：${row.status || thread?.status || '-'}`,
    `最近客户消息：${latestVisitor?.body_text || latestVisitor?.body || caseDetail?.last_customer_message || '-'}`,
    `下一步：${thread?.required_action || caseDetail?.required_action || handoff?.recommended_agent_action || '-'}`,
  ].join('\n')
  return (
    <Card className="v5-panel v5-side-panel">
      <CardHeader title="上下文与控制" subtitle="客户、AI 建议、接管、会话动作和运行证据。" />
      <CardBody>
        <Section title="Customer Profile" testId="webchat-template-customer-profile-section">
          <CustomerProfilePanel row={row} thread={thread} detail={caseDetail} />
        </Section>
        <Section title="AI Suggestions" testId="webchat-template-ai-suggestions-section">
          <AISuggestionsPanel suggestions={suggestions} onApply={onApplySuggestion} />
        </Section>
        <Section title="Handoff" testId="webchat-template-handoff-controls">
          <div className="v5-action-grid">
            {canAccept ? <Button variant="primary" disabled={busy} onClick={() => onAccept(handoff.id as number)}>接管</Button> : null}
            {canAccept ? <Button variant="secondary" disabled={busy} onClick={() => onDecline(handoff.id as number)}>跳过</Button> : null}
            {canRelease ? <Button variant="secondary" disabled={busy} onClick={() => onRelease(handoff.id as number)}>释放回队列</Button> : null}
            {canResume ? <Button variant="secondary" disabled={busy} onClick={() => onResume(handoff.id as number)}>恢复 AI</Button> : null}
            {forceVisible ? <Button variant="danger" disabled={busy || !forceAllowed} title={forceAllowed ? '强制接管 AI 会话' : '缺少 webchat.handoff.force_takeover 或当前队列项不可接管'} onClick={() => onForce(row.ticketId)}>强制接管</Button> : null}
          </div>
          {!handoff ? <p className="section-subtitle">当前没有开放的 handoff 请求；AI 活跃时需具备权限才能强制接管。</p> : null}
        </Section>
        <Section title="Session Actions" testId="webchat-template-session-actions-section">
          <SessionActionsPanel
            row={row}
            canEscalate={canEscalate}
            readStatePending={readStatePending}
            onCopyLink={onCopyLink}
            onMarkReadState={onMarkReadState}
            onEscalate={onEscalate}
            onApplySummary={() => onApplySuggestion(handoffSummary)}
          />
        </Section>
        <Section title="下一步动作 / Required action">
          <p className="v5-recommendation">{shortText(thread?.required_action || selectedHandoff?.recommended_agent_action || row.rawHandoff?.recommended_agent_action || '暂无明确下一步动作。')}</p>
        </Section>
        <Section title="系统证据" defaultOpen={false}>
          <CaseEvidencePanel detail={caseDetail} isLoading={evidenceLoading} isError={evidenceError} onRetry={onRetryEvidence} />
        </Section>
        <Section title="WebCall 状态" defaultOpen={false}>
          <AgentWebCallPanel
            ticketId={row.ticketId}
            ticketNo={row.ticketNo || undefined}
            conversationId={thread?.conversation_id || row.rawConversation?.conversation_id}
            visitorLabel={row.visitorLabel || 'Anonymous visitor'}
          />
        </Section>
        <Section title="实时状态" defaultOpen={false}>
          <div className="v5-realtime-card">{realtimeLabel}</div>
          <p className="section-subtitle">WebSocket 断开时自动回落到事件轮询，不阻塞人工处理。</p>
        </Section>
        <Section title="事件 / 审计预览" defaultOpen={false}>
          <ActionAuditPanel actions={thread?.actions ?? []} allowDebug={allowDebug} />
          <div className="v5-event-list">
            {(thread?.events ?? []).slice(-8).map((event) => <div key={event.id}><strong>{sanitizeDisplayText(event.event_type)}</strong><span>{formatDateTime(event.created_at)}</span></div>)}
            {!(thread?.events ?? []).length ? <p className="section-subtitle">暂无可展示事件。</p> : null}
          </div>
        </Section>
      </CardBody>
    </Card>
  )
}

export function WebchatInboxV5Page() {
  const client = useQueryClient()
  const session = useSession()
  const [view, setView] = useState<InboxView>('requested')
  const [search, setSearch] = useState('')
  const [activeFilters, setActiveFilters] = useState<Set<FilterKey>>(new Set())
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(() => {
    if (typeof window === 'undefined') return null
    const value = Number(new URLSearchParams(window.location.search).get('ticket_id'))
    return Number.isFinite(value) && value > 0 ? value : null
  })
  const [composerMode, setComposerMode] = useState<ComposerMode>('reply')
  const [reply, setReply] = useState('')
  const [internalNote, setInternalNote] = useState('')
  const [hasFactEvidence, setHasFactEvidence] = useState(false)
  const [safetyReview, setSafetyReview] = useState<SafetyReviewState | null>(null)
  const [toast, setToast] = useState<ToastState | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [contextOpen, setContextOpen] = useState(false)
  const [escalate, setEscalate] = useState<EscalateState>({ open: false, teamId: '', note: 'WebChat 会话需要主管跟进。' })
  const [lastEventId, setLastEventId] = useState(0)
  const [eventPollFailures, setEventPollFailures] = useState(0)

  const allowDebug = canViewWebchatDebug(session.data)
  const canForceTakeover = canForceWebchatHandoff(session.data)
  const canAttachEvidence = canUploadAttachment(session.data)
  const canEscalate = canEscalateTickets(session.data)
  const canSaveInternalNote = canWriteInternalNote(session.data)
  const canViewVoiceQueue = canViewWebcallVoiceQueue(session.data)
  const handoffQueryView: HandoffQueueView = view === 'all' ? 'requested' : view
  const realtimeHandoffView: RealtimeHandoffView = view === 'ai_active' || view === 'mine' ? view : 'requested'

  const conversations = useQuery({
    queryKey: ['webchatConversations'],
    queryFn: ({ signal }) => api.webchatConversations({ signal }),
    refetchInterval: 15000,
    retry: false,
  })
  const handoffQueue = useQuery({
    queryKey: ['webchatHandoffQueue', handoffQueryView],
    queryFn: ({ signal }) => api.webchatHandoffQueue({ view: handoffQueryView, limit: 80 }, { signal }),
    enabled: view !== 'all' && view !== 'closed',
    retry: false,
  })
  const outboundCapabilities = useQuery({
    queryKey: ['outboundChannelCapabilities'],
    queryFn: api.outboundChannelCapabilities,
    refetchInterval: 30000,
    retry: false,
  })
  const incomingVoiceSessions = useQuery({
    queryKey: ['webchatVoiceIncomingSessions', 'inbox-v5'],
    queryFn: ({ signal }) => api.webchatVoiceIncomingSessions({ status: 'incoming', limit: 50 }, { signal }),
    enabled: canViewVoiceQueue,
    refetchInterval: 4000,
    retry: false,
  })
  const teams = useQuery({
    queryKey: ['teams'],
    queryFn: api.teams,
    enabled: canEscalate,
    staleTime: 60000,
    retry: false,
  })
  const thread = useQuery({
    queryKey: ['webchatThread', selectedTicketId],
    queryFn: ({ signal }) => api.webchatThread(selectedTicketId as number, { signal }),
    enabled: !!selectedTicketId,
    retry: false,
  })
  const caseDetail = useQuery({
    queryKey: ['caseDetail', selectedTicketId],
    queryFn: ({ signal }) => {
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
      return api.caseDetail(selectedTicketId as number)
    },
    enabled: !!selectedTicketId,
    staleTime: 10000,
    retry: false,
  })

  const refreshWebchatState = useCallback(async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['webchatHandoffQueue'] }),
      client.invalidateQueries({ queryKey: ['webchatConversations'] }),
      client.invalidateQueries({ queryKey: ['webchatVoiceIncomingSessions'] }),
      selectedTicketId ? client.invalidateQueries({ queryKey: ['caseDetail', selectedTicketId] }) : Promise.resolve(),
      selectedTicketId ? client.invalidateQueries({ queryKey: ['webchatThread', selectedTicketId] }) : Promise.resolve(),
    ])
  }, [client, selectedTicketId])

  const applyRealtimeEvent = (event: WebchatRealtimeEvent) => {
    if (event.type === 'queue.snapshot' || event.type === 'queue.updated') {
      if (event.view && event.data) client.setQueryData<WebchatHandoffQueue>(['webchatHandoffQueue', event.view], event.data)
      return
    }
    if (event.type === 'message.created' && event.ticket_id) {
      client.setQueryData<WebchatThread | undefined>(['webchatThread', event.ticket_id], (old) => {
        if (!old || !event.message) return old
        if ((old.messages ?? []).some((msg) => msg.id === event.message?.id)) return old
        return { ...old, messages: [...(old.messages ?? []), event.message as WebchatMessage] }
      })
    }
    if (event.type.startsWith('handoff.') || event.type.startsWith('ai_turn.') || event.type === 'ai.resumed' || event.type === 'message.created') {
      if (event.ticket_id) void client.invalidateQueries({ queryKey: ['webchatThread', event.ticket_id] })
      void client.invalidateQueries({ queryKey: ['webchatConversations'] })
      void client.invalidateQueries({ queryKey: ['webchatHandoffQueue'] })
      void client.invalidateQueries({ queryKey: ['webchatVoiceIncomingSessions'] })
    }
  }

  const realtime = useWebchatRealtime({ enabled: true, selectedTicketId, handoffView: realtimeHandoffView, onEvent: applyRealtimeEvent })
  // polling fallback: after_id event polling remains active whenever WebSocket is disconnected.
  const events = useQuery({
    queryKey: ['webchatEvents', selectedTicketId, lastEventId],
    queryFn: ({ signal }) => api.webchatEvents(selectedTicketId as number, lastEventId, { signal }),
    enabled: !!selectedTicketId && !realtime.connected,
    refetchInterval: realtime.connected ? false : backoffMs(eventPollFailures, 2500, 30000),
    retry: false,
  })

  useEffect(() => {
    setLastEventId(0)
    setEventPollFailures(0)
    setSafetyReview(null)
    setHasFactEvidence(false)
    setInternalNote('')
  }, [selectedTicketId])
  useEffect(() => {
    if (safetyReview && safetyReview.body !== reply.trim()) setSafetyReview(null)
  }, [reply, safetyReview])
  useEffect(() => {
    if (events.isSuccess) setEventPollFailures(0)
    if (events.isError) setEventPollFailures((value) => Math.min(value + 1, 6))
  }, [events.isSuccess, events.isError, events.dataUpdatedAt, events.errorUpdatedAt])
  useEffect(() => {
    if (!selectedTicketId || !events.data?.events?.length) return
    setLastEventId(events.data.last_event_id || events.data.events[events.data.events.length - 1].id)
    void refreshWebchatState()
  }, [events.data, refreshWebchatState, selectedTicketId])

  const handoffRows = useMemo(() => (handoffQueue.data?.items ?? []).map(rowFromHandoff), [handoffQueue.data?.items])
  const conversationRows = useMemo(() => (conversations.data ?? []).map(rowFromConversation), [conversations.data])
  const rawRows = useMemo(() => {
    if (view === 'all') return conversationRows
    if (view === 'closed') return conversationRows.filter((row) => row.status && TERMINAL_TICKET_STATUSES.has(String(row.status)))
    return handoffRows
  }, [conversationRows, handoffRows, view])
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return rawRows.filter((row) => {
      if (needle && ![row.visitorLabel, row.ticketNo, row.title, row.lastMessage].some((value) => String(value || '').toLowerCase().includes(needle))) return false
      if (activeFilters.has('needs_human') && !row.needsHuman) return false
      if (activeFilters.has('ai_suspended') && !row.aiSuspended) return false
      if (activeFilters.has('unread') && !(row.unreadCount || row.markedUnread)) return false
      if (activeFilters.has('timeout')) {
        const waiting = row.rawHandoff?.waiting_seconds
        if (typeof waiting !== 'number' || waiting < 300) return false
      }
      return true
    })
  }, [activeFilters, rawRows, search])

  const selectTicket = (ticketId: number) => {
    setSelectedTicketId(ticketId)
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href)
      url.searchParams.set('ticket_id', String(ticketId))
      window.history.replaceState(null, '', url)
    }
  }

  useEffect(() => {
    if (!selectedTicketId && rows.length) selectTicket(rows[0].ticketId)
  }, [rows, selectedTicketId])

  useEffect(() => {
    if (!escalate.teamId && teams.data?.length) setEscalate((old) => ({ ...old, teamId: String(teams.data[0].id) }))
  }, [escalate.teamId, teams.data])

  const selectedRow = useMemo(() => rows.find((row) => row.ticketId === selectedTicketId) ?? conversationRows.find((row) => row.ticketId === selectedTicketId) ?? null, [conversationRows, rows, selectedTicketId])
  const threadData = thread.data
  const selectedHandoff = threadData?.handoff || selectedRow?.rawHandoff || null
  const webchatReplyChannel = useMemo(() => findReplyChannelCapability(outboundCapabilities.data?.channels, 'web_chat'), [outboundCapabilities.data])
  const webchatReplyEnabled = isCustomerSendableReplyChannel(webchatReplyChannel)
  const aiActiveWithoutOwnership = Boolean(
    !selectedHandoff &&
    selectedRow &&
    (selectedRow.aiPending || (selectedRow.aiStatus && AI_ACTIVE_STATUSES.has(selectedRow.aiStatus))) &&
    !selectedRow.aiSuspended,
  )
  const handoffReplyBlocked = Boolean(
    selectedHandoff
      ? !(selectedHandoff.status === 'accepted' && selectedHandoff.active_agent_id === session.data?.id)
      : aiActiveWithoutOwnership,
  )
  const sendDisabledReason = !webchatReplyEnabled
    ? outboundCapabilities.isError ? '回复通道能力接口不可用。' : webchatReplyChannel ? outboundChannelMissingText(webchatReplyChannel) : 'WebChat 回复通道缺失。'
    : handoffReplyBlocked
      ? aiActiveWithoutOwnership ? 'AI 正在处理该会话；请先强制接管后再回复。' : '该会话需要先由当前客服接管后才能回复。'
      : null
  const canSend = Boolean(selectedTicketId && reply.trim() && webchatReplyEnabled && !handoffReplyBlocked)

  const acceptMutation = useMutation({
    mutationFn: (requestId: number) => api.webchatAcceptHandoff(requestId),
    onSuccess: async (handoff) => { selectTicket(handoff.ticket_id); setToast({ message: '已接管会话，AI 已暂停。', tone: 'success' }); await refreshWebchatState() },
    onError: (err) => setToast({ message: apiErrorText(err, '接管失败'), tone: 'danger' }),
  })
  const declineMutation = useMutation({
    mutationFn: (requestId: number) => api.webchatDeclineHandoff(requestId, { reason_code: 'agent_skipped' }),
    onSuccess: async () => { setToast({ message: '已跳过；该请求仍保留给其他客服。', tone: 'success' }); await refreshWebchatState() },
    onError: (err) => setToast({ message: apiErrorText(err, '跳过失败'), tone: 'danger' }),
  })
  const forceMutation = useMutation({
    mutationFn: (ticketId: number) => api.webchatForceTakeover(ticketId, { reason_code: 'operator_forced_takeover' }),
    onSuccess: async (handoff) => { selectTicket(handoff.ticket_id); setToast({ message: '已强制接管，未完成 AI 回复已取消。', tone: 'success' }); await refreshWebchatState() },
    onError: (err) => setToast({ message: apiErrorText(err, '强制接管失败'), tone: 'danger' }),
  })
  const releaseMutation = useMutation({
    mutationFn: (requestId: number) => api.webchatReleaseHandoff(requestId),
    onSuccess: async () => { setToast({ message: '会话已释放回待接入队列，AI 仍保持暂停。', tone: 'success' }); await refreshWebchatState() },
    onError: (err) => setToast({ message: apiErrorText(err, '释放失败'), tone: 'danger' }),
  })
  const resumeAiMutation = useMutation({
    mutationFn: (requestId: number) => api.webchatResumeAi(requestId),
    onSuccess: async () => { setToast({ message: 'AI 已恢复，下一条客户消息可重新触发自动回复。', tone: 'success' }); await refreshWebchatState() },
    onError: (err) => setToast({ message: apiErrorText(err, '恢复 AI 失败'), tone: 'danger' }),
  })
  const replyMutation = useMutation({
    mutationFn: async (input?: ReplyMutationInput) => {
      if (!selectedTicketId) throw new Error('No ticket selected')
      const body = reply.trim()
      return api.webchatReply(selectedTicketId, { body, has_fact_evidence: hasFactEvidence, confirm_review: input?.confirmReview === true })
    },
    onSuccess: async () => { setReply(''); setHasFactEvidence(false); setSafetyReview(null); setToast({ message: 'WebChat 回复已发送并写入工单时间线。', tone: 'success' }); await refreshWebchatState() },
    onError: (err) => {
      const failedBody = reply.trim()
      const review = safetyReviewFromError(err, failedBody)
      if (review) {
        setSafetyReview(review)
        setToast({ message: '回复需要人工复核后才能继续发送。', tone: 'danger' })
        return
      }
      setSafetyReview(null)
      setToast({
        message: apiErrorText(err, '发送失败'),
        tone: 'danger',
        action: { label: '重试', onClick: () => setReply(failedBody) },
      })
    },
  })
  const attachmentMutation = useMutation({
    mutationFn: async (file: File) => {
      if (!selectedTicketId) throw new Error('No ticket selected')
      return api.uploadTicketAttachment(selectedTicketId, file, 'external')
    },
    onSuccess: async (attachment) => {
      setToast({ message: `已上传工单附件：${sanitizeDisplayText(attachment.file_name)}`, tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err) => setToast({ message: apiErrorText(err, '附件上传失败'), tone: 'danger' }),
  })
  const internalNoteMutation = useMutation({
    mutationFn: async () => {
      if (!selectedTicketId) throw new Error('No ticket selected')
      return api.addTicketInternalNote(selectedTicketId, { body: internalNote.trim() })
    },
    onSuccess: async () => {
      setInternalNote('')
      setToast({ message: '内部备注已写入工单 timeline。', tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err) => setToast({ message: apiErrorText(err, '内部备注保存失败'), tone: 'danger' }),
  })
  const escalateMutation = useMutation({
    mutationFn: async () => {
      if (!selectedTicketId) throw new Error('No ticket selected')
      const teamId = Number(escalate.teamId)
      if (!Number.isFinite(teamId) || teamId <= 0) throw new Error('请选择目标团队')
      return api.escalateTicket(selectedTicketId, { team_id: teamId, note: escalate.note.trim() })
    },
    onSuccess: async () => {
      setEscalate((old) => ({ ...old, open: false }))
      setToast({ message: '工单已升级，升级记录已写入审计。', tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err) => setToast({ message: apiErrorText(err, '升级失败'), tone: 'danger' }),
  })
  const readStateMutation = useMutation({
    mutationFn: async (markedUnread: boolean) => {
      if (!selectedTicketId) throw new Error('No ticket selected')
      return api.webchatReadState(selectedTicketId, { marked_unread: markedUnread })
    },
    onSuccess: async (state) => {
      setToast({ message: state.marked_unread ? '已标记为未读。' : '已标记为已读。', tone: 'success' })
      await refreshWebchatState()
    },
    onError: (err) => setToast({ message: apiErrorText(err, '更新已读状态失败'), tone: 'danger' }),
  })

  const copyConversationLink = async () => {
    if (!selectedTicketId || typeof window === 'undefined') return
    const url = new URL(window.location.href)
    url.searchParams.set('ticket_id', String(selectedTicketId))
    try {
      await navigator.clipboard.writeText(url.toString())
      setToast({ message: '会话链接已复制。', tone: 'success' })
    } catch {
      setToast({ message: url.toString(), tone: 'default' })
    }
  }

  const openEscalateDialog = () => {
    if (!canEscalate) {
      setToast({ message: '当前账号缺少 ticket.escalate，不能升级工单。', tone: 'danger' })
      return
    }
    if (!selectedTicketId) return
    setEscalate((old) => ({ ...old, open: true }))
  }

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault()
        if (composerMode === 'internal_note') {
          if (selectedTicketId && canSaveInternalNote && internalNote.trim() && !internalNoteMutation.isPending) internalNoteMutation.mutate()
        } else if (canSend && !replyMutation.isPending) replyMutation.mutate({})
      }
      if (event.altKey && /^[1-5]$/.test(event.key)) {
        event.preventDefault()
        const next = (['requested', 'mine', 'ai_active', 'all', 'closed'] as InboxView[])[Number(event.key) - 1]
        if (next) setView(next)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [canSaveInternalNote, canSend, composerMode, internalNote, internalNoteMutation, replyMutation, selectedTicketId])

  const incomingVoiceByTicket = useMemo(() => {
    const map = new Map<number, boolean>()
    for (const item of incomingVoiceSessions.data?.items ?? []) map.set(item.ticket_id, true)
    return map
  }, [incomingVoiceSessions.data?.items])
  const counts = useMemo<Partial<Record<InboxView, number>>>(() => ({
    requested: view === 'requested' ? handoffRows.length : undefined,
    mine: view === 'mine' ? handoffRows.length : undefined,
    ai_active: view === 'ai_active' ? handoffRows.length : undefined,
    all: conversationRows.length,
    closed: conversationRows.filter((row) => row.status && TERMINAL_TICKET_STATUSES.has(String(row.status))).length,
  }), [conversationRows, handoffRows, view])
  const busy = acceptMutation.isPending || declineMutation.isPending || forceMutation.isPending || releaseMutation.isPending || resumeAiMutation.isPending
  const toggleFilter = (key: FilterKey) => setActiveFilters((old) => {
    const next = new Set(old)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })
  const realtimeLabel = <RealtimePill connected={realtime.connected} status={realtime.status} />
  const insertReply = (text: string) => setReply((value) => value ? `${value}${text.length === 1 ? '' : '\n'}${text}` : text)
  const applySuggestion = (text: string) => {
    setComposerMode('reply')
    insertReply(text)
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="WebChat"
        title="人工 WebChat 收件箱"
        description="统一队列、接管控制、实时会话、工单证据和 WebCall 呼入处理，全部接入当前生产 API。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => void refreshWebchatState()}>刷新</Button>{realtimeLabel}</div>}
      />
      <div className="v5-shell">
        <UnifiedInbox
          rows={rows}
          selectedTicketId={selectedTicketId}
          onSelect={selectTicket}
          incomingVoiceByTicket={incomingVoiceByTicket}
          view={view}
          setView={setView}
          counts={counts}
          search={search}
          setSearch={setSearch}
          activeFilters={activeFilters}
          toggleFilter={toggleFilter}
        />
        <ConversationWorkspace
          selectedRow={selectedRow}
          thread={threadData}
          allowDebug={allowDebug}
          reply={reply}
          setReply={setReply}
          composerMode={composerMode}
          setComposerMode={setComposerMode}
          internalNote={internalNote}
          setInternalNote={setInternalNote}
          hasFactEvidence={hasFactEvidence}
          setHasFactEvidence={setHasFactEvidence}
          safetyReview={safetyReview}
          canSend={canSend}
          sendDisabledReason={sendDisabledReason}
          onSend={() => replyMutation.mutate({})}
          canSaveInternalNote={Boolean(selectedTicketId && canSaveInternalNote)}
          onSaveInternalNote={() => internalNoteMutation.mutate()}
          internalNotePending={internalNoteMutation.isPending}
          onConfirmReview={() => replyMutation.mutate({ confirmReview: true })}
          onDismissReview={() => setSafetyReview(null)}
          sendPending={replyMutation.isPending}
          onInsert={insertReply}
          onAttach={(file) => attachmentMutation.mutate(file)}
          canAttach={Boolean(selectedTicketId && canAttachEvidence)}
          attachmentPending={attachmentMutation.isPending}
          onCopyLink={() => void copyConversationLink()}
          onEscalate={openEscalateDialog}
          canEscalate={Boolean(selectedTicketId && canEscalate)}
          onMarkReadState={(markedUnread) => readStateMutation.mutate(markedUnread)}
          readStatePending={readStateMutation.isPending}
          onOpenContext={() => setContextOpen(true)}
        />
        <div className="v5-desktop-context">
          <ContextPanel
            row={selectedRow}
            thread={threadData}
            caseDetail={caseDetail.data}
            evidenceLoading={caseDetail.isLoading}
            evidenceError={caseDetail.isError}
            onRetryEvidence={() => void caseDetail.refetch()}
            selectedHandoff={selectedHandoff}
            realtimeLabel={realtimeLabel}
            allowDebug={allowDebug}
            canForceTakeover={canForceTakeover}
            busy={busy}
            onCopyLink={() => void copyConversationLink()}
            onMarkReadState={(markedUnread) => readStateMutation.mutate(markedUnread)}
            readStatePending={readStateMutation.isPending}
            onEscalate={openEscalateDialog}
            canEscalate={Boolean(selectedTicketId && canEscalate)}
            onApplySuggestion={applySuggestion}
            webchatReplyEnabled={webchatReplyEnabled}
            sendDisabledReason={sendDisabledReason}
            onAccept={(requestId) => acceptMutation.mutate(requestId)}
            onDecline={(requestId) => declineMutation.mutate(requestId)}
            onForce={(ticketId) => setConfirm({ title: '确认强制接管？', body: '该操作会暂停 AI，并取消未完成 AI 回复。', tone: 'danger', confirmLabel: '强制接管', onConfirm: () => forceMutation.mutate(ticketId) })}
            onRelease={(requestId) => setConfirm({ title: '释放回队列？', body: '释放后你将不能继续回复，其他客服可接入。', confirmLabel: '释放回队列', onConfirm: () => releaseMutation.mutate(requestId) })}
            onResume={(requestId) => setConfirm({ title: '恢复 AI？', body: '恢复后下一条客户消息可重新触发 AI 自动回复。', confirmLabel: '恢复 AI', onConfirm: () => resumeAiMutation.mutate(requestId) })}
          />
        </div>
      </div>
      <Dialog.Root open={contextOpen} onOpenChange={setContextOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="v5-dialog-overlay" />
          <Dialog.Content className="v5-context-drawer">
            <Dialog.Title className="v5-dialog-title">会话上下文</Dialog.Title>
            <ContextPanel
              row={selectedRow}
              thread={threadData}
              caseDetail={caseDetail.data}
              evidenceLoading={caseDetail.isLoading}
              evidenceError={caseDetail.isError}
              onRetryEvidence={() => void caseDetail.refetch()}
              selectedHandoff={selectedHandoff}
              realtimeLabel={realtimeLabel}
              allowDebug={allowDebug}
              canForceTakeover={canForceTakeover}
              busy={busy}
              onCopyLink={() => void copyConversationLink()}
              onMarkReadState={(markedUnread) => readStateMutation.mutate(markedUnread)}
              readStatePending={readStateMutation.isPending}
              onEscalate={openEscalateDialog}
              canEscalate={Boolean(selectedTicketId && canEscalate)}
              onApplySuggestion={applySuggestion}
              webchatReplyEnabled={webchatReplyEnabled}
              sendDisabledReason={sendDisabledReason}
              onAccept={(requestId) => acceptMutation.mutate(requestId)}
              onDecline={(requestId) => declineMutation.mutate(requestId)}
              onForce={(ticketId) => setConfirm({ title: '确认强制接管？', body: '该操作会暂停 AI，并取消未完成 AI 回复。', tone: 'danger', confirmLabel: '强制接管', onConfirm: () => forceMutation.mutate(ticketId) })}
              onRelease={(requestId) => releaseMutation.mutate(requestId)}
              onResume={(requestId) => resumeAiMutation.mutate(requestId)}
            />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <ConfirmDialog state={confirm} onClose={() => setConfirm(null)} />
      <EscalateDialog
        state={escalate}
        setState={setEscalate}
        teams={teams.data ?? []}
        busy={escalateMutation.isPending}
        onSubmit={() => escalateMutation.mutate()}
      />
      {toast ? <Toast message={toast.message} tone={toast.tone} action={toast.action} onClose={() => setToast(null)} /> : null}
      {(conversations.isLoading || handoffQueue.isLoading || thread.isLoading || caseDetail.isLoading || incomingVoiceSessions.isLoading || teams.isLoading) ? <div className="v5-floating-loading"><Skeleton lines={1} /></div> : null}
    </AppShell>
  )
}
