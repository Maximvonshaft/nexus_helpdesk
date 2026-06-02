import { useEffect, useMemo, useState, type ChangeEvent } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { BadgeTone, CaseDetail, EmailMailboxQueueItem, OutboundChannelCapability, SystemAttachment } from '@/lib/types'
import { formatDateTime, labelize, marketLabel, priorityTone, sanitizeDisplayText, statusTone } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
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
const MAX_EMAIL_ATTACHMENTS = 10

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

function channelTone(capability?: OutboundChannelCapability): BadgeTone {
  if (!capability) return 'default'
  if (capability.supports_send) return 'success'
  if (capability.configured) return 'warning'
  return 'danger'
}

function queueReasonTone(reason: string): BadgeTone {
  if (reason === 'customer_reply_received') return 'success'
  if (['outbound_dead', 'outbound_failed'].includes(reason)) return 'danger'
  if (['outbound_pending', 'draft_saved'].includes(reason)) return 'warning'
  return 'default'
}

function timelineTitle(item: Record<string, unknown>) {
  const sourceType = String(item.source_type || item.kind || '')
  if (sourceType === 'outbound_message') return 'Email / 外部回复'
  if (sourceType === 'inbound_email') return '客户来信（Email）'
  if (sourceType === 'comment') return '客户消息'
  if (sourceType === 'internal_note') return '内部备注'
  if (sourceType === 'ticket_event') return '工单事件'
  return labelize(sourceType || 'timeline')
}

function timelineBody(item: Record<string, unknown>) {
  return sanitizeDisplayText(String(item.body || item.summary || item.note || item.subject || item.event_type || item.id || ''))
}

function EmailQueueCard({ item, selected, onSelect }: { item: EmailMailboxQueueItem; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`queue-card ${selected ? 'selected' : ''}`} onClick={onSelect}>
      <div className="badges">
        <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>
        <Badge tone={priorityTone(item.priority)}>{labelize(item.priority)}</Badge>
        <Badge tone={queueReasonTone(item.queue_reason)}>{labelize(item.queue_reason)}</Badge>
        <Badge>{labelize(item.direction)}</Badge>
      </div>
      <div className="queue-card-title">#{item.ticket_id} {sanitizeDisplayText(item.title)}</div>
      <div className="queue-card-meta">{sanitizeDisplayText(item.customer_name || item.customer_email || '未填写客户')} · {marketLabel(item.market_code, item.country_code)}</div>
      <div className="queue-card-meta">{sanitizeDisplayText(item.tracking_number || '无运单号')} · {formatDateTime(item.last_message_at || item.updated_at)}</div>
      {item.last_message_subject ? <div className="queue-card-meta">{sanitizeDisplayText(item.last_message_subject)}</div> : null}
    </button>
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
  const [confirmSend, setConfirmSend] = useState(false)

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
    setConfirmSend(false)
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
      if (!attachment.visibility || attachment.visibility === 'external') byId.set(attachment.id, attachment)
    }
    return Array.from(byId.values())
  }, [activeCase.attachments, uploadedAttachments])

  const recipient = emailRecipient(activeCase)
  const canSaveDraft = canAccess(session.data, emailDraftAccess)
  const canSendEmail = canAccess(session.data, emailSendAccess)
  const attachmentBlocked = attachmentIds.length > 0 && !emailCapability?.supports_attachments
  const maxAttachmentsReached = attachmentIds.length >= MAX_EMAIL_ATTACHMENTS
  const canDraft = Boolean(canSaveDraft && subject.trim() && body.trim() && !attachmentBlocked)
  const canSend = Boolean(canSendEmail && emailCapability?.supports_send && recipient && subject.trim() && body.trim() && !attachmentBlocked)

  function toggleAttachment(attachmentId: number) {
    setAttachmentIds((current) => {
      if (current.includes(attachmentId)) return current.filter((item) => item !== attachmentId)
      if (current.length >= MAX_EMAIL_ATTACHMENTS) return current
      return [...current, attachmentId]
    })
  }

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => Promise.all(files.map((file) => api.uploadTicketAttachment(activeCase.id, file, 'external'))),
    onSuccess: async (attachments) => {
      setUploadedAttachments((current) => {
        const byId = new Map<number, SystemAttachment>()
        for (const attachment of [...current, ...attachments]) byId.set(attachment.id, attachment)
        return Array.from(byId.values())
      })
      setAttachmentIds((current) => Array.from(new Set([...current, ...attachments.map((attachment) => attachment.id)])))
      onToast({ message: `已上传并选中 ${attachments.length} 个 Email 附件`, tone: 'success' })
      await Promise.all([
        client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] }),
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
        client.invalidateQueries({ queryKey: ['emailWorkbenchCases'] }),
      ])
    },
    onError: (err: Error) => onToast({ message: err.message || '保存 Email 草稿失败', tone: 'danger' }),
  })

  const sendMutation = useMutation({
    mutationFn: () => api.sendOutboundMessage(activeCase.id, { channel: 'email', subject: subject.trim(), body: body.trim(), attachment_ids: attachmentIds }),
    onSuccess: async () => {
      onToast({ message: 'Email 已进入发送链路，发送结果会回写到 timeline。', tone: 'success' })
      setBody('')
      setAttachmentIds([])
      setConfirmSend(false)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] }),
        client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] }),
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
          {emailCapability?.missing?.length ? <ErrorSummary title="发送前需要补齐" errors={emailCapability.missing.map(labelize)} /> : null}
          {attachmentBlocked ? <ErrorSummary title="附件发送未启用" errors={['当前 Email 发送能力不支持附件，请取消选择后再保存或发送。']} /> : null}
          <Field label="Email 主题" required>
            <Input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="请输入客户能识别的邮件主题" />
          </Field>
          <Field label="回复正文" required hint="保存草稿和发送都会进入 ticket timeline；不要写入内部排障细节或密钥。">
            <Textarea value={body} onChange={(event) => setBody(event.target.value)} rows={9} placeholder="输入要发送给客户的 Email 回复" />
          </Field>
          <Field label="上传外部附件" hint={`上传成功后会自动选中。最多 ${MAX_EMAIL_ATTACHMENTS} 个附件。`} disabledReason={!emailCapability?.supports_attachments ? '当前 Email channel capability 未启用附件发送' : undefined}>
            <Input type="file" multiple disabled={!emailCapability?.supports_attachments || uploadMutation.isPending} onChange={handleAttachmentUpload} />
          </Field>
          {uploadMutation.isPending ? <Skeleton lines={1} /> : null}
          {availableAttachments.length ? (
            <div className="stack">
              {availableAttachments.map((attachment) => (
                <label key={attachment.id} className="toggle-row">
                  <input type="checkbox" checked={attachmentIds.includes(attachment.id)} disabled={!attachmentIds.includes(attachment.id) && maxAttachmentsReached} onChange={() => toggleAttachment(attachment.id)} />
                  <span>{sanitizeDisplayText(attachment.file_name)} · {sanitizeDisplayText(attachment.mime_type || 'file')}</span>
                </label>
              ))}
            </div>
          ) : <EmptyState title="暂无可发送附件" description="工单外部附件会显示在这里，并随 Email draft/send 绑定到 outbound message。" />}
          <div className="button-row">
            <Button onClick={() => draftMutation.mutate()} disabled={!canDraft || draftMutation.isPending}>{draftMutation.isPending ? '保存中...' : '保存草稿'}</Button>
            <Button variant="primary" onClick={() => setConfirmSend(true)} disabled={!canSend || sendMutation.isPending}>{sendMutation.isPending ? '发送中...' : '发送 Email'}</Button>
          </div>
          <ConfirmDialog
            open={confirmSend}
            title="确认发送外部 Email？"
            description={`收件人：${recipient || '未配置'}；主题：${subject.trim() || '未填写'}；附件：${attachmentIds.length} 个。`}
            consequence="该操作会进入外部发送链路并写入工单 timeline。发送前请确认收件人、主题、正文和附件均正确。"
            confirmLabel="确认发送 Email"
            tone="danger"
            pending={sendMutation.isPending}
            onCancel={() => setConfirmSend(false)}
            onConfirm={() => sendMutation.mutate()}
          />
        </>
      ) : null}
    </div>
  )
}

function EmailWorkbenchPage() {
  const autoRefresh = useAutoRefresh(true)
  const client = useQueryClient()
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const mailboxQueue = useQuery({
    queryKey: ['emailWorkbenchCases', query, status],
    queryFn: () => api.emailMailboxQueue({ q: query || undefined, status: status || undefined }),
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })
  const rows = mailboxQueue.data?.items ?? []

  useEffect(() => {
    if (!selectedTicketId && rows.length) setSelectedTicketId(rows[0].ticket_id)
  }, [rows, selectedTicketId])

  const detail = useQuery({
    queryKey: ['caseDetail', selectedTicketId],
    queryFn: () => api.caseDetail(selectedTicketId as number),
    enabled: !!selectedTicketId,
    refetchInterval: autoRefresh.enabled ? 10000 : false,
  })
  const timeline = useQuery({
    queryKey: ['ticketTimeline', selectedTicketId],
    queryFn: () => api.ticketTimeline(selectedTicketId as number, { limit: 30 }),
    enabled: !!selectedTicketId,
    refetchInterval: autoRefresh.enabled ? 10000 : false,
  })

  const activeCase = detail.data
  const timelineItems = timeline.data?.items ?? []
  const emailReadyCount = mailboxQueue.data?.total ?? rows.length
  const openCount = rows.filter((item) => !['resolved', 'closed', 'canceled', 'cancelled'].includes(String(item.status))).length
  const overdueCount = rows.filter((item) => item.overdue).length

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/email']}>
        <PageHeader
          eyebrow="Email"
          title="Email 客服处理台"
          description="一线客服只处理邮件队列、邮件线程、回复草稿和发送确认；IMAP、回执和重排等运维动作已从本页面降噪。"
          actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={() => client.invalidateQueries()} disabled={mailboxQueue.isFetching}>{mailboxQueue.isFetching ? '刷新中...' : '立即刷新'}</Button></div>}
        />
        <div className="metrics-grid">
          <MetricCard label="Email 候选" value={emailReadyCount} hint="mailbox projection 队列项" />
          <MetricCard label="待处理" value={openCount} hint="未进入 resolved/closed/canceled" />
          <MetricCard label="SLA 风险" value={overdueCount} hint="overdue tickets" />
          <MetricCard label="当前工单" value={selectedTicketId ?? '-'} hint="draft/send bind to ticket outbound API" />
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
        <div className="page-grid workspace email-agent-workspace">
          <Card>
            <CardHeader title="Email Queue" subtitle="从后端 mailbox projection 读取入站、出站和 ticket marker 队列项。" />
            <CardBody>
              <div className="stack">
                {mailboxQueue.isLoading ? <Skeleton lines={6} /> : null}
                {mailboxQueue.isError ? <ErrorSummary title="无法加载 Email mailbox 队列" errors={[mailboxQueue.error?.message || '请稍后重试']} /> : null}
                {rows.map((item) => <EmailQueueCard key={item.id} item={item} selected={selectedTicketId === item.ticket_id} onSelect={() => setSelectedTicketId(item.ticket_id)} />)}
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
                    <div className="badges"><Badge tone={statusTone(activeCase.status)}>{labelize(activeCase.status)}</Badge><Badge tone={priorityTone(activeCase.priority)}>{labelize(activeCase.priority)}</Badge></div>
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
                        <div className="message-head"><strong>{timelineTitle(item as Record<string, unknown>)}</strong><span>{formatDateTime(String(item.created_at || ''))}</span></div>
                        <div>{timelineBody(item as Record<string, unknown>)}</div>
                      </div>
                    ))}
                    {timeline.isLoading ? <Skeleton lines={4} /> : null}
                    {!timeline.isLoading && !timelineItems.length ? <EmptyState title="暂无 timeline" description="发送或保存动作成功后，后端应把证据写回 timeline。" /> : null}
                  </div>
                </div>
              ) : <EmptyState title="请选择一条 Email 队列项" description="选择后展示客户上下文、timeline 和回复草稿。" />}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Reply Composer / Guardrails" subtitle="调用真实 outbound draft/send API；发送前展示最终确认。" />
            <CardBody>{activeCase ? <EmailComposer activeCase={activeCase} onToast={setToast} /> : <EmptyState title="等待选择工单" description="Email 回复必须绑定 ticket，才能写入审计和 timeline。" />}</CardBody>
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
