import { useEffect, useMemo, useState, type ChangeEvent } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { BadgeTone, CaseDetail, CaseListItem, OutboundChannelCapability, SystemAttachment } from '@/lib/types'
import { formatDateTime, labelize, marketLabel, priorityTone, sanitizeDisplayText, statusTone } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useSession } from '@/hooks/useAuth'
import { CAPABILITIES, canAccess, routeAccess, type AccessRequirement } from '@/lib/rbac'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'

const emailDraftAccess = { allOf: [CAPABILITIES.outboundDraftSave] } satisfies AccessRequirement
const emailSendAccess = { allOf: [CAPABILITIES.outboundSend] } satisfies AccessRequirement
const emailRetryAccess = { allOf: [CAPABILITIES.runtimeManage] } satisfies AccessRequirement
const EMAIL_QUEUE_TOKENS = new Set(['email', 'mail', 'smtp', 'imap', 'pop3'])
const MAX_EMAIL_ATTACHMENTS = 10

function isEmailCandidate(item: CaseListItem) {
  const text = [item.source_channel, item.category, item.sub_category]
    .map((value) => String(value || '').toLowerCase().replace(/\be[-_\s]?mail\b/g, 'email'))
    .join(' ')
  return text.split(/[^a-z0-9]+/).some((token) => EMAIL_QUEUE_TOKENS.has(token))
}

function emailRecipient(activeCase: CaseDetail) {
  return activeCase.preferred_reply_contact || activeCase.customer?.email || ''
}

function defaultSubject(activeCase: CaseDetail) {
  return activeCase.title?.trim() || `Ticket ${activeCase.id} customer reply`
}

function defaultBody(activeCase: CaseDetail) {
  return [
    activeCase.customer_update,
    activeCase.required_action ? `Next step: ${activeCase.required_action}` : null,
    activeCase.missing_fields ? `Missing information: ${activeCase.missing_fields}` : null,
  ].filter(Boolean).join('\n\n')
}

function channelTone(capability?: OutboundChannelCapability) {
  if (!capability) return 'default'
  if (capability.supports_send) return 'success'
  if (capability.configured) return 'warning'
  return 'danger'
}

function EmailComposer({
  activeCase,
  onToast,
}: {
  activeCase: CaseDetail
  onToast: (toast: { message: string; tone?: 'default' | 'danger' | 'success' }) => void
}) {
  const session = useSession()
  const client = useQueryClient()
  const [subject, setSubject] = useState(defaultSubject(activeCase))
  const [body, setBody] = useState(defaultBody(activeCase))
  const [attachmentIds, setAttachmentIds] = useState<number[]>([])
  const [uploadedAttachments, setUploadedAttachments] = useState<SystemAttachment[]>([])
  const [confirmExternal, setConfirmExternal] = useState(false)

  const capabilities = useQuery({
    queryKey: ['ticketOutboundChannelCapabilities', activeCase.id],
    queryFn: () => api.ticketOutboundChannelCapabilities(activeCase.id),
    enabled: !!activeCase.id,
  })

  useEffect(() => {
    setSubject(defaultSubject(activeCase))
    setBody(defaultBody(activeCase))
    setAttachmentIds([])
    setUploadedAttachments([])
    setConfirmExternal(false)
    // Reset only when the ticket changes; live refetches must not wipe an operator draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCase.id])

  const emailCapability = useMemo(
    () => (capabilities.data?.channels ?? []).find((item) => item.channel === 'email'),
    [capabilities.data?.channels],
  )
  const availableAttachments = useMemo(() => {
    const byId = new Map<number, SystemAttachment>()
    for (const attachment of [...(activeCase.attachments ?? []), ...uploadedAttachments]) {
      if (!attachment.visibility || attachment.visibility === 'external') {
        byId.set(attachment.id, attachment)
      }
    }
    return Array.from(byId.values())
  }, [activeCase.attachments, uploadedAttachments])
  const recipient = emailRecipient(activeCase)
  const canSaveDraft = canAccess(session.data, emailDraftAccess)
  const canSendEmail = canAccess(session.data, emailSendAccess)
  const attachmentBlocked = attachmentIds.length > 0 && !emailCapability?.supports_attachments
  const maxAttachmentsReached = attachmentIds.length >= MAX_EMAIL_ATTACHMENTS
  const canDraft = Boolean(canSaveDraft && subject.trim() && body.trim() && !attachmentBlocked)
  const canSend = Boolean(canSendEmail && emailCapability?.supports_send && recipient && subject.trim() && body.trim() && confirmExternal && !attachmentBlocked)

  function toggleAttachment(attachmentId: number) {
    setAttachmentIds((current) => {
      if (current.includes(attachmentId)) return current.filter((item) => item !== attachmentId)
      if (current.length >= MAX_EMAIL_ATTACHMENTS) return current
      return [...current, attachmentId]
    })
  }

  function mergeAttachmentIds(ids: number[]) {
    setAttachmentIds((current) => Array.from(new Set([...current, ...ids])))
  }

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => Promise.all(files.map((file) => api.uploadTicketAttachment(activeCase.id, file, 'external'))),
    onSuccess: async (attachments) => {
      setUploadedAttachments((current) => {
        const byId = new Map<number, SystemAttachment>()
        for (const attachment of [...current, ...attachments]) byId.set(attachment.id, attachment)
        return Array.from(byId.values())
      })
      mergeAttachmentIds(attachments.map((attachment) => attachment.id))
      onToast({ message: `已上传并选中 ${attachments.length} 个 Email 附件`, tone: 'success' })
      await Promise.all([
        client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['cases'] }),
        client.invalidateQueries({ queryKey: ['emailWorkbenchCases'] }),
      ])
    },
    onError: (err: Error) => onToast({ message: err.message || '上传 Email 附件失败', tone: 'danger' }),
  })

  function handleAttachmentUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files ?? [])
    event.currentTarget.value = ''
    if (!files.length) return
    if (files.length > MAX_EMAIL_ATTACHMENTS - attachmentIds.length) {
      onToast({ message: `Email 最多绑定 ${MAX_EMAIL_ATTACHMENTS} 个附件，请先取消部分已选附件。`, tone: 'danger' })
      return
    }
    uploadMutation.mutate(files)
  }

  const draftMutation = useMutation({
    mutationFn: () => api.saveOutboundDraft(activeCase.id, { channel: 'email', subject: subject.trim(), body: body.trim(), attachment_ids: attachmentIds }),
    onSuccess: async () => {
      onToast({ message: 'Email 草稿已保存到工单 timeline', tone: 'success' })
      await Promise.all([
        client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['cases'] }),
        client.invalidateQueries({ queryKey: ['emailWorkbenchCases'] }),
      ])
    },
    onError: (err: Error) => onToast({ message: err.message || '保存 Email 草稿失败', tone: 'danger' }),
  })

  const sendMutation = useMutation({
    mutationFn: () => api.sendOutboundMessage(activeCase.id, { channel: 'email', subject: subject.trim(), body: body.trim(), attachment_ids: attachmentIds }),
    onSuccess: async (result) => {
      const semantics = String(result.delivery_semantics || '')
      onToast({
        message: semantics === 'external_provider_send' ? 'Email 已进入外部发送队列' : 'Email 回复已发送或记录',
        tone: 'success',
      })
      setBody('')
      setAttachmentIds([])
      setConfirmExternal(false)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['cases'] }),
        client.invalidateQueries({ queryKey: ['emailWorkbenchCases'] }),
      ])
    },
    onError: (err: Error) => onToast({ message: err.message || '发送 Email 失败', tone: 'danger' }),
  })

  return (
    <div className="stack" data-testid="email-workbench-composer">
      {capabilities.isLoading ? <Skeleton lines={4} /> : null}
      {capabilities.isError ? <EmptyState title="无法加载 Email 发送能力" description="请稍后重试，或联系主管检查 outbound channel capability。" /> : null}
      {!capabilities.isLoading && !capabilities.isError ? (
        <>
          <div className="kv-grid">
            <div className="kv"><label>Email 收件人</label><div>{sanitizeDisplayText(recipient || '未配置')}</div></div>
            <div className="kv"><label>发送链路</label><div>{emailCapability?.external_send ? 'SMTP 外部发送' : '未启用外部 Email'}</div></div>
          </div>
          <div className="badges">
            <Badge tone={channelTone(emailCapability)}>{emailCapability?.supports_send ? 'Email 可发送' : 'Email 不可发送'}</Badge>
            {emailCapability?.status ? <Badge>{labelize(emailCapability.status)}</Badge> : null}
            {emailCapability?.external_send ? <Badge tone="warning">外部 provider</Badge> : null}
            <Badge tone={emailCapability?.supports_attachments ? 'success' : 'warning'}>attachments {emailCapability?.supports_attachments ? '可发送' : '未启用'}</Badge>
            <Badge tone={canSaveDraft ? 'success' : 'warning'}>draft.save {canSaveDraft ? '已授权' : '未授权'}</Badge>
            <Badge tone={canSendEmail ? 'success' : 'warning'}>outbound.send {canSendEmail ? '已授权' : '未授权'}</Badge>
          </div>
          {emailCapability?.missing?.length ? (
            <ErrorSummary title="发送前需要补齐" errors={emailCapability.missing.map(labelize)} />
          ) : null}
          {attachmentBlocked ? <ErrorSummary title="附件发送未启用" errors={['当前 Email 发送能力不支持附件，请取消选择后再保存或发送。']} /> : null}
          <Field label="Email 主题" required>
            <Input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="请输入客户能识别的邮件主题" />
          </Field>
          <Field label="回复正文" required hint="保存草稿和发送都会进入 ticket timeline/ticket event audit；不要写入内部排障细节或密钥。">
            <Textarea value={body} onChange={(event) => setBody(event.target.value)} rows={9} placeholder="输入要发送给客户的 Email 回复" />
          </Field>
          <div className="stack" data-testid="email-workbench-attachments">
            <div className="section-subtitle">可发送附件</div>
            <Field
              label="上传外部附件"
              hint={`上传成功后会自动选中，并随保存草稿或发送进入 outbound message。最多 ${MAX_EMAIL_ATTACHMENTS} 个附件。`}
              disabledReason={!emailCapability?.supports_attachments ? '当前 Email channel capability 未启用附件发送' : undefined}
            >
              <Input
                data-testid="email-workbench-attachment-upload"
                type="file"
                multiple
                disabled={!emailCapability?.supports_attachments || uploadMutation.isPending}
                onChange={handleAttachmentUpload}
              />
            </Field>
            {uploadMutation.isPending ? <Skeleton lines={1} /> : null}
            {availableAttachments.length ? (
              <div className="stack">
                {availableAttachments.map((attachment) => (
                  <label key={attachment.id} className="toggle-row">
                    <input
                      type="checkbox"
                      checked={attachmentIds.includes(attachment.id)}
                      disabled={!attachmentIds.includes(attachment.id) && maxAttachmentsReached}
                      onChange={() => toggleAttachment(attachment.id)}
                    />
                    <span>{sanitizeDisplayText(attachment.file_name)} · {sanitizeDisplayText(attachment.mime_type || 'file')}</span>
                  </label>
                ))}
              </div>
            ) : (
              <EmptyState title="暂无可发送附件" description="工单外部附件会显示在这里，并随 Email draft/send 绑定到 outbound message。" />
            )}
          </div>
          <label className="toggle-row">
            <input type="checkbox" checked={confirmExternal} onChange={(event) => setConfirmExternal(event.target.checked)} />
            <span>我确认这是 SMTP 外部邮件发送，收件人、主题和正文已核对。</span>
          </label>
          <div className="button-row">
            <Button onClick={() => draftMutation.mutate()} disabled={!canDraft || draftMutation.isPending}>
              {draftMutation.isPending ? '保存中...' : '保存草稿'}
            </Button>
            <Button variant="primary" onClick={() => sendMutation.mutate()} disabled={!canSend || sendMutation.isPending}>
              {sendMutation.isPending ? '发送中...' : '发送 Email'}
            </Button>
          </div>
        </>
      ) : null}
    </div>
  )
}

function timelineTitle(item: Record<string, unknown>) {
  const sourceType = String(item.source_type || item.kind || '')
  if (sourceType === 'outbound_message') return 'Email/外部回复'
  if (sourceType === 'comment') return '客户来信'
  if (sourceType === 'internal_note') return '内部备注'
  if (sourceType === 'ticket_event') return '工单事件'
  return labelize(sourceType || 'timeline')
}

function timelineBody(item: Record<string, unknown>) {
  return sanitizeDisplayText(String(item.body || item.summary || item.note || item.event_type || item.id || ''))
}

function timelinePayload(item: Record<string, unknown>) {
  return item.payload && typeof item.payload === 'object' ? item.payload as Record<string, unknown> : {}
}

function providerField(item: Record<string, unknown>, key: string) {
  const payload = timelinePayload(item)
  return item[key] ?? payload[key] ?? null
}

function isOutboundTimelineItem(item: Record<string, unknown>) {
  return String(item.source_type || item.kind || '') === 'outbound_message'
}

function outboundStatusTone(status: string): BadgeTone {
  if (status === 'sent') return 'success'
  if (status === 'dead' || status === 'failed') return 'danger'
  if (status === 'pending' || status === 'processing') return 'warning'
  return 'default'
}

function OutboundProviderStatus({
  item,
  canRequeue,
  pending,
  onRequeue,
}: {
  item: Record<string, unknown>
  canRequeue: boolean
  pending: boolean
  onRequeue: (messageId: number) => void
}) {
  const statusValue = String(providerField(item, 'status') || '-')
  const providerStatus = String(providerField(item, 'provider_status') || '-')
  const failureReason = String(providerField(item, 'failure_reason') || providerField(item, 'failure_code') || '')
  const nextRetryAt = String(providerField(item, 'next_retry_at') || '')
  const sentAt = String(providerField(item, 'sent_at') || '')
  const mailboxThreadId = String(providerField(item, 'mailbox_thread_id') || '')
  const mailboxMessageId = String(providerField(item, 'mailbox_message_id') || '')
  const mailboxReferences = String(providerField(item, 'mailbox_references') || '')
  const retryCount = Number(providerField(item, 'retry_count') ?? 0)
  const maxRetries = Number(providerField(item, 'max_retries') ?? 0)
  const messageId = Number(item.source_id)
  const dead = statusValue === 'dead'

  return (
    <div className="stack" data-testid="email-provider-delivery-status">
      <div className="badges">
        <Badge tone={outboundStatusTone(statusValue)}>delivery {labelize(statusValue)}</Badge>
        <Badge>{sanitizeDisplayText(providerStatus)}</Badge>
        {retryCount || maxRetries ? <Badge>retry {retryCount}/{maxRetries}</Badge> : null}
        {nextRetryAt ? <Badge tone="warning">next {formatDateTime(nextRetryAt)}</Badge> : null}
        {sentAt ? <Badge tone="success">sent {formatDateTime(sentAt)}</Badge> : null}
      </div>
      {mailboxThreadId || mailboxMessageId ? (
        <div className="section-subtitle">
          {mailboxThreadId ? <>thread {sanitizeDisplayText(mailboxThreadId)}</> : null}
          {mailboxThreadId && mailboxMessageId ? ' · ' : null}
          {mailboxMessageId ? <>message-id {sanitizeDisplayText(mailboxMessageId)}</> : null}
        </div>
      ) : null}
      {mailboxReferences ? <div className="section-subtitle">references {sanitizeDisplayText(mailboxReferences)}</div> : null}
      {failureReason ? <div className="section-subtitle">{sanitizeDisplayText(failureReason)}</div> : null}
      {dead ? (
        <div className="button-row">
          <Button
            variant="secondary"
            disabled={!canRequeue || pending || !Number.isFinite(messageId)}
            onClick={() => onRequeue(messageId)}
          >
            {pending ? '重排中...' : '重排发送'}
          </Button>
          {!canRequeue ? <span className="section-subtitle">需要 runtime.manage 权限</span> : null}
        </div>
      ) : null}
    </div>
  )
}

function EmailWorkbenchPage() {
  const autoRefresh = useAutoRefresh(true)
  const client = useQueryClient()
  const session = useSession()
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const cases = useQuery({
    queryKey: ['emailWorkbenchCases', query, status],
    queryFn: () => api.cases({ q: query || undefined, status: status || undefined }),
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })

  const rows = useMemo(() => {
    const items = cases.data ?? []
    const emailItems = items.filter(isEmailCandidate)
    return emailItems.length ? emailItems : items
  }, [cases.data])

  useEffect(() => {
    if (!selectedId && rows.length) setSelectedId(rows[0].id)
  }, [rows, selectedId])

  const detail = useQuery({
    queryKey: ['caseDetail', selectedId],
    queryFn: () => api.caseDetail(selectedId as number),
    enabled: !!selectedId,
    refetchInterval: autoRefresh.enabled ? 10000 : false,
  })

  const timeline = useQuery({
    queryKey: ['ticketTimeline', selectedId],
    queryFn: () => api.ticketTimeline(selectedId as number, { limit: 30 }),
    enabled: !!selectedId,
    refetchInterval: autoRefresh.enabled ? 10000 : false,
  })

  const activeCase = detail.data
  const canRequeueOutbound = canAccess(session.data, emailRetryAccess)
  const emailReadyCount = rows.filter((item) => isEmailCandidate(item)).length
  const openCount = rows.filter((item) => !['resolved', 'closed', 'canceled', 'cancelled'].includes(String(item.status))).length
  const overdueCount = rows.filter((item) => item.overdue).length
  const requeueMutation = useMutation({
    mutationFn: (messageId: number) => api.requeueOutboundMessage(messageId),
    onSuccess: async (result) => {
      setToast({ message: `已重排 outbound #${result.message_id ?? ''}`, tone: 'success' })
      await Promise.all([
        client.invalidateQueries({ queryKey: ['caseDetail', selectedId] }),
        client.invalidateQueries({ queryKey: ['ticketTimeline', selectedId] }),
        client.invalidateQueries({ queryKey: ['cases'] }),
        client.invalidateQueries({ queryKey: ['emailWorkbenchCases'] }),
      ])
    },
    onError: (err: Error) => setToast({ message: err.message || '重排 outbound 失败', tone: 'danger' }),
  })

  function handleRequeueOutbound(messageId: number) {
    if (!Number.isFinite(messageId)) return
    if (!window.confirm(`确认重排 outbound message #${messageId}？后端会重新进入发送队列并记录审计。`)) return
    requeueMutation.mutate(messageId)
  }

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/email']}>
        <PageHeader
          eyebrow="Email"
          title="Email 客服处理台"
          description="把模板中的 Email 队列、客户历史、回复草稿、SMTP 发送语义、失败阻断和审计回写落到真实 ticket/outbound API 上。"
          actions={
            <div className="button-row">
              <Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>
                {autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}
              </Button>
              <Button onClick={() => client.invalidateQueries()} disabled={cases.isFetching}>
                {cases.isFetching ? '刷新中...' : '立即刷新'}
              </Button>
            </div>
          }
        />

        <div className="metrics-grid">
          <MetricCard label="Email 候选" value={emailReadyCount || rows.length} hint="email/source-channel 优先，否则回退 ticket queue" />
          <MetricCard label="待处理" value={openCount} hint="未进入 resolved/closed/canceled" />
          <MetricCard label="SLA 风险" value={overdueCount} hint="overdue tickets" />
          <MetricCard label="当前工单" value={selectedId ?? '-'} hint="draft/send bind to ticket outbound API" />
        </div>

        <div className="workspace-toolbar">
          <Input placeholder="搜索邮件、客户、工单、运单号..." value={query} onChange={(event) => setQuery(event.target.value)} />
          <Select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部状态</option>
            <option value="in_progress">处理中</option>
            <option value="waiting_customer">待客户回复</option>
            <option value="resolved">已解决</option>
          </Select>
          <div className="workspace-toolbar-meta">共 {rows.length} 条</div>
        </div>

        <div className="page-grid workspace">
          <Card>
            <CardHeader title="Email Queue" subtitle="按 ticket 队列承载 Email 处理，避免绕过既有权限、证据和 timeline。" />
            <CardBody>
              <div className="stack">
                {cases.isLoading ? <Skeleton lines={6} /> : null}
                {cases.isError ? <div className="message" data-role="agent">无法加载 Email 队列。</div> : null}
                {rows.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`queue-card ${selectedId === item.id ? 'selected' : ''}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <div className="badges">
                      <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>
                      <Badge tone={priorityTone(item.priority)}>{labelize(item.priority)}</Badge>
                      {isEmailCandidate(item) ? <Badge tone="success">Email</Badge> : <Badge>Ticket</Badge>}
                    </div>
                    <div className="queue-card-title">#{item.id} {sanitizeDisplayText(item.title)}</div>
                    <div className="queue-card-meta">{sanitizeDisplayText(item.customer_name || '未填写客户')} · {marketLabel(item.market_code, item.country_code)}</div>
                    <div className="queue-card-meta">更新 {formatDateTime(item.updated_at)} · 来源 {sanitizeDisplayText(item.source_channel || '-')}</div>
                  </button>
                ))}
                {!rows.length && !cases.isLoading ? <EmptyState title="没有 Email 队列项" description="当前筛选没有可处理的邮件或工单。" /> : null}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Email Thread / Timeline" subtitle="客户来信、外部回复、内部备注和工单事件统一从 ticket timeline 读取。" />
            <CardBody>
              {detail.isLoading && !activeCase ? <Skeleton lines={8} /> : null}
              {activeCase ? (
                <div className="stack">
                  <div className="hero-block">
                    <div>
                      <div className="hero-title">#{activeCase.id} · {sanitizeDisplayText(activeCase.title)}</div>
                      <div className="section-subtitle">{sanitizeDisplayText(activeCase.customer_name || activeCase.customer?.name || '未填写客户')} · 更新时间 {formatDateTime(activeCase.updated_at)}</div>
                    </div>
                    <div className="badges">
                      <Badge tone={statusTone(activeCase.status)}>{labelize(activeCase.status)}</Badge>
                      <Badge tone={priorityTone(activeCase.priority)}>{labelize(activeCase.priority)}</Badge>
                    </div>
                  </div>
                  <div className="kv-grid">
                    <div className="kv"><label>Email</label><div>{sanitizeDisplayText(emailRecipient(activeCase) || '未配置')}</div></div>
                    <div className="kv"><label>首选渠道</label><div>{sanitizeDisplayText(activeCase.preferred_reply_channel || '-')}</div></div>
                    <div className="kv"><label>运单号</label><div>{sanitizeDisplayText(activeCase.tracking_number || '-')}</div></div>
                    <div className="kv"><label>市场</label><div>{marketLabel(activeCase.market_code, activeCase.country_code)}</div></div>
                  </div>
                  <div className="message" data-role="user">{sanitizeDisplayText(activeCase.last_customer_message || activeCase.customer_request || activeCase.issue_summary || '暂无客户来信摘要。')}</div>
                  <div className="timeline">
                    {(timeline.data?.items ?? []).map((item, index) => (
                      <div key={String(item.id || index)} className="message" data-role={String(item.source_type) === 'comment' ? 'user' : 'agent'}>
                        <div className="message-head">
                          <strong>{timelineTitle(item as Record<string, unknown>)}</strong>
                          <span>{formatDateTime(String(item.created_at || ''))}</span>
                        </div>
                        <div>{timelineBody(item as Record<string, unknown>)}</div>
                        {isOutboundTimelineItem(item as Record<string, unknown>) ? (
                          <OutboundProviderStatus
                            item={item as Record<string, unknown>}
                            canRequeue={canRequeueOutbound}
                            pending={requeueMutation.isPending}
                            onRequeue={handleRequeueOutbound}
                          />
                        ) : null}
                      </div>
                    ))}
                    {timeline.isLoading ? <Skeleton lines={4} /> : null}
                    {!timeline.isLoading && !(timeline.data?.items ?? []).length ? <EmptyState title="暂无 timeline" description="发送或保存动作成功后，后端应把证据写回 timeline。" /> : null}
                  </div>
                </div>
              ) : (
                <EmptyState title="请选择一条 Email 队列项" description="选择后展示客户上下文、timeline 和回复草稿。" />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Reply Composer / Guardrails" subtitle="调用真实 outbound draft/send API；SMTP 账号配置仍在系统配置中维护。" />
            <CardBody>
              {activeCase ? (
                <EmailComposer activeCase={activeCase} onToast={setToast} />
              ) : (
                <EmptyState title="等待选择工单" description="Email 回复必须绑定 ticket，才能写入审计和 timeline。" />
              )}
            </CardBody>
          </Card>
        </div>
        {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/email',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: EmailWorkbenchPage,
})
