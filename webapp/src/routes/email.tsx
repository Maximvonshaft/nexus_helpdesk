import { useEffect, useMemo, useState, type ChangeEvent } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { BadgeTone, CaseDetail, OutboundChannelCapability, SystemAttachment } from '@/lib/types'
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
const emailInboundSyncAccess = { allOf: [CAPABILITIES.runtimeManage] } satisfies AccessRequirement
const emailDeliveryReceiptAccess = { allOf: [CAPABILITIES.runtimeManage] } satisfies AccessRequirement
const emailMailboxSyncAccess = { allOf: [CAPABILITIES.runtimeManage] } satisfies AccessRequirement
const MAX_EMAIL_ATTACHMENTS = 10
const EMAIL_DELIVERY_STATUSES = ['accepted', 'delivered', 'opened', 'deferred', 'bounced', 'failed', 'rejected', 'complained'] as const
type EmailDeliveryStatus = (typeof EMAIL_DELIVERY_STATUSES)[number]

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

function defaultInboundSubject(activeCase: CaseDetail) {
  return `Re: ${defaultSubject(activeCase).replace(/^re:\s*/i, '')}`
}

function channelTone(capability?: OutboundChannelCapability) {
  if (!capability) return 'default'
  if (capability.supports_send) return 'success'
  if (capability.configured) return 'warning'
  return 'danger'
}

function mailboxSyncTone(value?: string | null): BadgeTone {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'ok' || normalized === 'configured') return 'success'
  if (normalized === 'error' || normalized === 'not_configured') return 'danger'
  if (normalized === 'pending' || normalized === 'processing') return 'warning'
  return 'default'
}

function EmailMailboxDaemon({
  onToast,
}: {
  onToast: (toast: { message: string; tone?: 'default' | 'danger' | 'success' }) => void
}) {
  const session = useSession()
  const client = useQueryClient()
  const canManageSync = canAccess(session.data, emailMailboxSyncAccess)
  const status = useQuery({
    queryKey: ['emailMailboxSyncStatus'],
    queryFn: api.emailMailboxSyncStatus,
    enabled: canManageSync,
    refetchInterval: canManageSync ? 30000 : false,
  })
  const enqueueMutation = useMutation({
    mutationFn: () => api.enqueueEmailMailboxSync({}),
    onSuccess: async (result) => {
      onToast({ message: `已入队 ${result.enqueued} 个 mailbox sync job`, tone: 'success' })
      await Promise.all([
        client.invalidateQueries({ queryKey: ['emailMailboxSyncStatus'] }),
        client.invalidateQueries({ queryKey: ['emailWorkbenchCases'] }),
      ])
    },
    onError: (err: Error) => onToast({ message: err.message || '入队 mailbox sync 失败', tone: 'danger' }),
  })
  if (!canManageSync) {
    return (
      <div className="stack" data-testid="email-mailbox-daemon">
        <Badge tone="warning">runtime.manage 未授权</Badge>
        <EmptyState title="Mailbox polling 需要 runtime.manage" description="入站 IMAP daemon 状态和手动同步只对运行时管理员开放。" />
      </div>
    )
  }
  const data = status.data
  return (
    <div className="stack" data-testid="email-mailbox-daemon">
      <div className="badges">
        <Badge tone={data?.daemon_enabled ? 'success' : 'warning'}>daemon {data?.daemon_enabled ? 'enabled' : 'disabled'}</Badge>
        <Badge>{data?.interval_seconds ?? '-'}s interval</Badge>
        <Badge tone={(data?.pending_jobs ?? 0) > 0 ? 'warning' : 'default'}>{data?.pending_jobs ?? 0} pending</Badge>
        <Badge tone={(data?.dead_jobs ?? 0) > 0 ? 'danger' : 'default'}>{data?.dead_jobs ?? 0} dead</Badge>
      </div>
      {status.isLoading ? <Skeleton lines={3} /> : null}
      {status.isError ? <ErrorSummary title="无法加载 mailbox daemon 状态" errors={[status.error?.message || '请稍后重试']} /> : null}
      {data ? (
        <>
          <div className="kv-grid">
            <div className="kv"><label>启用账号</label><div>{data.enabled_accounts}</div></div>
            <div className="kv"><label>配置完整</label><div>{data.configured_accounts}</div></div>
            <div className="kv"><label>生成时间</label><div>{formatDateTime(data.generated_at)}</div></div>
            <div className="kv"><label>队列</label><div>{data.pending_jobs} pending · {data.dead_jobs} dead</div></div>
          </div>
          <div className="stack">
            {data.accounts.map((account) => (
              <div key={account.account_id} className="message" data-role="agent">
                <div className="message-head">
                  <strong>{sanitizeDisplayText(account.display_name || account.from_address)}</strong>
                  <Badge tone={mailboxSyncTone(account.imap_last_status)}>{sanitizeDisplayText(account.imap_last_status || (account.configured ? 'configured' : 'not_configured'))}</Badge>
                </div>
                <div>{sanitizeDisplayText(account.imap_host || '-')} · {sanitizeDisplayText(account.imap_mailbox || 'INBOX')} · cursor {sanitizeDisplayText(account.imap_sync_cursor || '-')}</div>
                <div className="section-subtitle">last seen {formatDateTime(account.imap_last_seen_at)} · job {account.imap_last_sync_job_id ?? '-'}</div>
                {account.imap_last_error ? <div className="section-subtitle">{sanitizeDisplayText(account.imap_last_error)}</div> : null}
              </div>
            ))}
            {!data.accounts.length ? <EmptyState title="暂无 mailbox sync 账号" description="在 Outbound Email 账号配置中启用 IMAP 后会出现在这里。" /> : null}
          </div>
        </>
      ) : null}
      <div className="button-row">
        <Button onClick={() => enqueueMutation.mutate()} disabled={enqueueMutation.isPending || status.isLoading}>
          {enqueueMutation.isPending ? '入队中...' : '立即同步 mailbox'}
        </Button>
      </div>
    </div>
  )
}

function EmailInboundSync({
  activeCase,
  onToast,
}: {
  activeCase: CaseDetail
  onToast: (toast: { message: string; tone?: 'default' | 'danger' | 'success' }) => void
}) {
  const session = useSession()
  const client = useQueryClient()
  const [fromAddress, setFromAddress] = useState(emailRecipient(activeCase))
  const [provider, setProvider] = useState('manual')
  const [providerMessageId, setProviderMessageId] = useState('')
  const [mailboxMessageId, setMailboxMessageId] = useState('')
  const [mailboxReferences, setMailboxReferences] = useState('')
  const [subject, setSubject] = useState(defaultInboundSubject(activeCase))
  const [body, setBody] = useState('')
  const canSyncInbound = canAccess(session.data, emailInboundSyncAccess)
  const canSubmit = Boolean(canSyncInbound && fromAddress.trim() && body.trim())

  useEffect(() => {
    setFromAddress(emailRecipient(activeCase))
    setProvider('manual')
    setProviderMessageId('')
    setMailboxMessageId('')
    setMailboxReferences('')
    setSubject(defaultInboundSubject(activeCase))
    setBody('')
    // Reset only when the selected ticket changes; live refetches must not wipe an operator replay body.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCase.id])

  const ingestMutation = useMutation({
    mutationFn: () => api.ingestInboundEmail(activeCase.id, {
      from_address: fromAddress.trim(),
      provider: provider.trim() || 'manual',
      provider_message_id: providerMessageId.trim() || null,
      mailbox_message_id: mailboxMessageId.trim() || null,
      mailbox_references: mailboxReferences.trim() || null,
      subject: subject.trim() || null,
      body: body.trim(),
    }),
    onSuccess: async (result) => {
      onToast({ message: result.created ? 'Inbound Email 已写入 timeline/audit' : 'Inbound Email 已存在，已返回现有记录', tone: 'success' })
      if (result.created) setBody('')
      await Promise.all([
        client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['cases'] }),
        client.invalidateQueries({ queryKey: ['emailWorkbenchCases'] }),
      ])
    },
    onError: (err: Error) => onToast({ message: err.message || '记录 inbound Email 失败', tone: 'danger' }),
  })

  return (
    <div className="stack" data-testid="email-inbound-sync">
      <div className="badges">
        <Badge tone={canSyncInbound ? 'success' : 'warning'}>runtime.manage {canSyncInbound ? '已授权' : '未授权'}</Badge>
        <Badge>ticket timeline/audit</Badge>
      </div>
      <div className="kv-grid">
        <Field label="From" required><Input type="email" value={fromAddress} onChange={(event) => setFromAddress(event.target.value)} placeholder="customer@example.com" /></Field>
        <Field label="Provider"><Input value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="imap, webhook, manual" /></Field>
      </div>
      <Field label="Email 主题"><Input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="客户来信主题" /></Field>
      <div className="kv-grid">
        <Field label="Provider message id"><Input value={providerMessageId} onChange={(event) => setProviderMessageId(event.target.value)} placeholder="provider-message-id" /></Field>
        <Field label="Mailbox message id"><Input value={mailboxMessageId} onChange={(event) => setMailboxMessageId(event.target.value)} placeholder="<message@example.com>" /></Field>
      </div>
      <Field label="References / In-Reply-To"><Input value={mailboxReferences} onChange={(event) => setMailboxReferences(event.target.value)} placeholder="<nexusdesk-ticket-...@nexusdesk.local>" /></Field>
      <Field label="客户来信正文" required>
        <Textarea value={body} onChange={(event) => setBody(event.target.value)} rows={6} placeholder="粘贴 provider 收到的客户邮件正文" />
      </Field>
      <div className="button-row">
        <Button onClick={() => ingestMutation.mutate()} disabled={!canSubmit || ingestMutation.isPending}>
          {ingestMutation.isPending ? '写入中...' : '记录入站邮件'}
        </Button>
        {!canSyncInbound ? <span className="section-subtitle">需要 runtime.manage 权限</span> : null}
      </div>
    </div>
  )
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
  if (sourceType === 'inbound_email') return '客户来信（Email）'
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

function isInboundEmailTimelineItem(item: Record<string, unknown>) {
  return String(item.source_type || item.kind || '') === 'inbound_email'
}

function outboundTimelineMessageId(item: Record<string, unknown>) {
  const value = Number(item.source_id)
  return Number.isFinite(value) && value > 0 ? value : null
}

function outboundStatusTone(status: string): BadgeTone {
  if (status === 'sent') return 'success'
  if (status === 'dead' || status === 'failed') return 'danger'
  if (status === 'pending' || status === 'processing') return 'warning'
  return 'default'
}

function receiptTone(status: string): BadgeTone {
  if (['accepted', 'delivered', 'opened'].includes(status)) return 'success'
  if (status === 'deferred') return 'warning'
  if (['bounced', 'failed', 'rejected', 'complained'].includes(status)) return 'danger'
  return 'default'
}

function queueReasonTone(reason: string): BadgeTone {
  if (reason === 'customer_reply_received') return 'success'
  if (['outbound_dead', 'outbound_failed'].includes(reason)) return 'danger'
  if (['outbound_pending', 'draft_saved'].includes(reason)) return 'warning'
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
  const deliveryStatus = String(providerField(item, 'delivery_status') || '')
  const deliveryEventType = String(providerField(item, 'delivery_event_type') || '')
  const deliveryReceiptProvider = String(providerField(item, 'delivery_receipt_provider') || '')
  const deliveryReceiptId = String(providerField(item, 'delivery_receipt_id') || '')
  const deliveryReceiptAt = String(providerField(item, 'delivery_receipt_at') || '')
  const deliveryDetail = String(providerField(item, 'delivery_detail') || '')
  const retryCount = Number(providerField(item, 'retry_count') ?? 0)
  const maxRetries = Number(providerField(item, 'max_retries') ?? 0)
  const messageId = Number(item.source_id)
  const dead = statusValue === 'dead'

  return (
    <div className="stack" data-testid="email-provider-delivery-status">
      <div className="badges">
        <Badge tone={outboundStatusTone(statusValue)}>delivery {labelize(statusValue)}</Badge>
        <Badge>{sanitizeDisplayText(providerStatus)}</Badge>
        {deliveryStatus ? <Badge tone={receiptTone(deliveryStatus)}>receipt {labelize(deliveryStatus)}</Badge> : null}
        {deliveryReceiptProvider ? <Badge>{sanitizeDisplayText(deliveryReceiptProvider)}</Badge> : null}
        {retryCount || maxRetries ? <Badge>retry {retryCount}/{maxRetries}</Badge> : null}
        {nextRetryAt ? <Badge tone="warning">next {formatDateTime(nextRetryAt)}</Badge> : null}
        {sentAt ? <Badge tone="success">sent {formatDateTime(sentAt)}</Badge> : null}
        {deliveryReceiptAt ? <Badge>receipt {formatDateTime(deliveryReceiptAt)}</Badge> : null}
      </div>
      {deliveryEventType || deliveryReceiptId ? (
        <div className="section-subtitle">receipt {sanitizeDisplayText(deliveryEventType || '-')} {deliveryReceiptId ? `· ${sanitizeDisplayText(deliveryReceiptId)}` : ''}</div>
      ) : null}
      {mailboxThreadId || mailboxMessageId ? (
        <div className="section-subtitle">
          {mailboxThreadId ? <>thread {sanitizeDisplayText(mailboxThreadId)}</> : null}
          {mailboxThreadId && mailboxMessageId ? ' · ' : null}
          {mailboxMessageId ? <>message-id {sanitizeDisplayText(mailboxMessageId)}</> : null}
        </div>
      ) : null}
      {mailboxReferences ? <div className="section-subtitle">references {sanitizeDisplayText(mailboxReferences)}</div> : null}
      {deliveryDetail ? <div className="section-subtitle">{sanitizeDisplayText(deliveryDetail)}</div> : null}
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

function InboundProviderStatus({ item }: { item: Record<string, unknown> }) {
  const provider = String(providerField(item, 'provider') || '-')
  const providerMessageId = String(providerField(item, 'provider_message_id') || '')
  const fromAddress = String(providerField(item, 'from_address') || '')
  const mailboxThreadId = String(providerField(item, 'mailbox_thread_id') || '')
  const mailboxMessageId = String(providerField(item, 'mailbox_message_id') || '')
  const mailboxReferences = String(providerField(item, 'mailbox_references') || '')
  const receivedAt = String(providerField(item, 'received_at') || item.created_at || '')

  return (
    <div className="stack" data-testid="email-inbound-provider-status">
      <div className="badges">
        <Badge tone="success">inbound sync</Badge>
        <Badge>{sanitizeDisplayText(provider)}</Badge>
        {fromAddress ? <Badge>{sanitizeDisplayText(fromAddress)}</Badge> : null}
        {receivedAt ? <Badge>{formatDateTime(receivedAt)}</Badge> : null}
      </div>
      {providerMessageId ? <div className="section-subtitle">provider-message {sanitizeDisplayText(providerMessageId)}</div> : null}
      {mailboxThreadId || mailboxMessageId ? (
        <div className="section-subtitle">
          {mailboxThreadId ? <>thread {sanitizeDisplayText(mailboxThreadId)}</> : null}
          {mailboxThreadId && mailboxMessageId ? ' · ' : null}
          {mailboxMessageId ? <>message-id {sanitizeDisplayText(mailboxMessageId)}</> : null}
        </div>
      ) : null}
      {mailboxReferences ? <div className="section-subtitle">references {sanitizeDisplayText(mailboxReferences)}</div> : null}
    </div>
  )
}

function EmailDeliveryReceiptRecorder({
  activeCase,
  outboundItems,
  onToast,
}: {
  activeCase: CaseDetail
  outboundItems: Record<string, unknown>[]
  onToast: (toast: { message: string; tone?: 'default' | 'danger' | 'success' }) => void
}) {
  const session = useSession()
  const client = useQueryClient()
  const firstMessageId = useMemo(() => outboundTimelineMessageId(outboundItems[0] ?? {}) ?? null, [outboundItems])
  const [messageId, setMessageId] = useState<number | null>(firstMessageId)
  const [deliveryStatus, setDeliveryStatus] = useState<EmailDeliveryStatus>('delivered')
  const [provider, setProvider] = useState('manual')
  const [providerEventType, setProviderEventType] = useState('delivered')
  const [providerEventId, setProviderEventId] = useState('')
  const [providerStatus, setProviderStatus] = useState('')
  const [detail, setDetail] = useState('')
  const [failureCode, setFailureCode] = useState('')
  const [failureReason, setFailureReason] = useState('')
  const canRecordReceipt = canAccess(session.data, emailDeliveryReceiptAccess)
  const canSubmit = Boolean(canRecordReceipt && messageId && deliveryStatus)
  const selectedItem = outboundItems.find((item) => outboundTimelineMessageId(item) === messageId)

  useEffect(() => {
    setMessageId(firstMessageId)
    setDeliveryStatus('delivered')
    setProvider('manual')
    setProviderEventType('delivered')
    setProviderEventId('')
    setProviderStatus('')
    setDetail('')
    setFailureCode('')
    setFailureReason('')
  }, [activeCase.id, firstMessageId])

  const receiptMutation = useMutation({
    mutationFn: () => api.recordEmailDeliveryReceipt(activeCase.id, messageId as number, {
      delivery_status: deliveryStatus,
      provider: provider.trim() || 'manual',
      provider_event_type: providerEventType.trim() || deliveryStatus,
      provider_event_id: providerEventId.trim() || null,
      provider_status: providerStatus.trim() || null,
      detail: detail.trim() || null,
      failure_code: failureCode.trim() || null,
      failure_reason: failureReason.trim() || null,
    }),
    onSuccess: async (result) => {
      onToast({ message: result.created ? `Delivery receipt ${result.delivery_status} 已写入 timeline/audit` : 'Delivery receipt 已存在，未重复写入', tone: 'success' })
      await Promise.all([
        client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['cases'] }),
        client.invalidateQueries({ queryKey: ['emailWorkbenchCases'] }),
      ])
    },
    onError: (err: Error) => onToast({ message: err.message || '记录 delivery receipt 失败', tone: 'danger' }),
  })

  return (
    <div className="stack" data-testid="email-delivery-receipt-recorder">
      <div className="badges">
        <Badge tone={canRecordReceipt ? 'success' : 'warning'}>runtime.manage {canRecordReceipt ? '已授权' : '未授权'}</Badge>
        <Badge>provider receipt</Badge>
      </div>
      {!outboundItems.length ? <EmptyState title="暂无 outbound message" description="保存草稿或发送 Email 后，才能写入 provider delivery receipt。" /> : null}
      {outboundItems.length ? (
        <>
          <Field label="Outbound message" required>
            <Select value={messageId ? String(messageId) : ''} onChange={(event) => setMessageId(Number(event.target.value) || null)}>
              {outboundItems.map((item) => {
                const id = outboundTimelineMessageId(item)
                return id ? <option key={id} value={id}>#{id} · {sanitizeDisplayText(String(providerField(item, 'subject') || providerField(item, 'status') || 'outbound'))}</option> : null
              })}
            </Select>
          </Field>
          {selectedItem ? (
            <div className="section-subtitle">
              当前 provider 状态：{sanitizeDisplayText(String(providerField(selectedItem, 'provider_status') || '-'))}
            </div>
          ) : null}
          <div className="kv-grid">
            <Field label="Receipt status" required>
              <Select value={deliveryStatus} onChange={(event) => {
                const next = event.target.value as EmailDeliveryStatus
                setDeliveryStatus(next)
                setProviderEventType(next)
              }}>
                {EMAIL_DELIVERY_STATUSES.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}
              </Select>
            </Field>
            <Field label="Provider"><Input value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="smtp, ses, mailgun, manual" /></Field>
          </div>
          <div className="kv-grid">
            <Field label="Provider event"><Input value={providerEventType} onChange={(event) => setProviderEventType(event.target.value)} placeholder="delivered" /></Field>
            <Field label="Event id"><Input value={providerEventId} onChange={(event) => setProviderEventId(event.target.value)} placeholder="receipt-event-id" /></Field>
          </div>
          <Field label="Provider status"><Input value={providerStatus} onChange={(event) => setProviderStatus(event.target.value)} placeholder="provider raw status, optional" /></Field>
          <Field label="Receipt detail"><Textarea value={detail} onChange={(event) => setDetail(event.target.value)} rows={3} placeholder="provider 回执摘要，不要填写密钥或完整原始 header" /></Field>
          {['deferred', 'bounced', 'failed', 'rejected', 'complained'].includes(deliveryStatus) ? (
            <div className="kv-grid">
              <Field label="Failure code"><Input value={failureCode} onChange={(event) => setFailureCode(event.target.value)} placeholder={deliveryStatus} /></Field>
              <Field label="Failure reason"><Input value={failureReason} onChange={(event) => setFailureReason(event.target.value)} placeholder="provider failure reason" /></Field>
            </div>
          ) : null}
          <div className="button-row">
            <Button onClick={() => receiptMutation.mutate()} disabled={!canSubmit || receiptMutation.isPending}>
              {receiptMutation.isPending ? '写入中...' : '记录回执'}
            </Button>
            {!canRecordReceipt ? <span className="section-subtitle">需要 runtime.manage 权限</span> : null}
          </div>
        </>
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

  const mailboxQueue = useQuery({
    queryKey: ['emailWorkbenchCases', query, status],
    queryFn: () => api.emailMailboxQueue({ q: query || undefined, status: status || undefined }),
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })

  const rows = useMemo(() => {
    return mailboxQueue.data?.items ?? []
  }, [mailboxQueue.data?.items])

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
  const timelineItems = useMemo(() => timeline.data?.items ?? [], [timeline.data?.items])
  const outboundReceiptItems = useMemo(() => timelineItems.filter((item) => isOutboundTimelineItem(item as Record<string, unknown>)) as Record<string, unknown>[], [timelineItems])

  const activeCase = detail.data
  const canRequeueOutbound = canAccess(session.data, emailRetryAccess)
  const emailReadyCount = mailboxQueue.data?.total ?? rows.length
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
              <Button onClick={() => client.invalidateQueries()} disabled={mailboxQueue.isFetching}>
                {mailboxQueue.isFetching ? '刷新中...' : '立即刷新'}
              </Button>
            </div>
          }
        />

        <div className="metrics-grid">
          <MetricCard label="Email 候选" value={emailReadyCount} hint="独立 mailbox projection，不再前端筛 ticket queue" />
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
            <CardHeader title="Email Queue" subtitle="从后端 mailbox projection 读取入站、出站和 ticket marker 队列项。" />
            <CardBody>
              <div className="stack">
                {mailboxQueue.isLoading ? <Skeleton lines={6} /> : null}
                {mailboxQueue.isError ? <div className="message" data-role="agent">无法加载 Email mailbox 队列。</div> : null}
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
                      <Badge tone={queueReasonTone(item.queue_reason)}>{labelize(item.queue_reason)}</Badge>
                      <Badge>{labelize(item.queue_source)}</Badge>
                      {item.delivery_status ? <Badge tone={receiptTone(item.delivery_status)}>{labelize(item.delivery_status)}</Badge> : null}
                    </div>
                    <div className="queue-card-title">#{item.id} {sanitizeDisplayText(item.title)}</div>
                    <div className="queue-card-meta">{sanitizeDisplayText(item.customer_name || '未填写客户')} · {marketLabel(item.market_code, item.country_code)}</div>
                    <div className="queue-card-meta">
                      {item.last_message_at ? `邮件 ${formatDateTime(item.last_message_at)}` : `更新 ${formatDateTime(item.updated_at)}`} · {sanitizeDisplayText(item.last_message_subject || item.source_channel || '-')}
                    </div>
                    {item.mailbox_thread_id || item.mailbox_message_id ? (
                      <div className="queue-card-meta">
                        {item.mailbox_thread_id ? `thread ${sanitizeDisplayText(item.mailbox_thread_id)}` : null}
                        {item.mailbox_thread_id && item.mailbox_message_id ? ' · ' : null}
                        {item.mailbox_message_id ? `message ${sanitizeDisplayText(item.mailbox_message_id)}` : null}
                      </div>
                    ) : null}
                  </button>
                ))}
                {!rows.length && !mailboxQueue.isLoading ? <EmptyState title="没有 Email 队列项" description="当前筛选没有可处理的 mailbox projection 项。" /> : null}
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
                    {timelineItems.map((item, index) => (
                      <div key={String(item.id || index)} className="message" data-role={['comment', 'inbound_email'].includes(String(item.source_type)) ? 'user' : 'agent'}>
                        <div className="message-head">
                          <strong>{timelineTitle(item as Record<string, unknown>)}</strong>
                          <span>{formatDateTime(String(item.created_at || ''))}</span>
                        </div>
                        <div>{timelineBody(item as Record<string, unknown>)}</div>
                        {isInboundEmailTimelineItem(item as Record<string, unknown>) ? (
                          <InboundProviderStatus item={item as Record<string, unknown>} />
                        ) : null}
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
                    {!timeline.isLoading && !timelineItems.length ? <EmptyState title="暂无 timeline" description="发送或保存动作成功后，后端应把证据写回 timeline。" /> : null}
                  </div>
                </div>
              ) : (
                <EmptyState title="请选择一条 Email 队列项" description="选择后展示客户上下文、timeline 和回复草稿。" />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Mailbox Polling / IMAP Daemon" subtitle="读取 runtime mailbox sync 状态，并可手动把已配置的 IMAP 账号入队。" />
            <CardBody>
              <EmailMailboxDaemon onToast={setToast} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Inbound Sync / Audit" subtitle="把 provider 收到的客户来信写入 ticket timeline、mailbox thread 和审计链路。" />
            <CardBody>
              {activeCase ? (
                <EmailInboundSync activeCase={activeCase} onToast={setToast} />
              ) : (
                <EmptyState title="等待选择工单" description="Inbound Email 必须绑定 ticket，才能合并 mailbox thread 并写入审计。" />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Delivery Receipt / Provider Event" subtitle="把 provider delivered/bounced/deferred 回执写回 outbound message、timeline 和审计链路。" />
            <CardBody>
              {activeCase ? (
                <EmailDeliveryReceiptRecorder activeCase={activeCase} outboundItems={outboundReceiptItems} onToast={setToast} />
              ) : (
                <EmptyState title="等待选择工单" description="Delivery receipt 必须绑定 Email outbound message，才能更新 provider 状态和审计。" />
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
