import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const emailRoute = readFileSync(resolve(root, 'src/routes/email.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')

test('email workbench route uses unified routeAccess RBAC semantics', () => {
  assert.match(router, /EmailRoute/)
  assert.match(router, /@\/routes\/email/)
  assert.match(emailRoute, /path: '\/email'/)
  assert.match(rbac, /'\/email': \{ allOf: \[CAPABILITIES\.ticketRead\], anyOf: \[CAPABILITIES\.outboundDraftSave, CAPABILITIES\.outboundSend\] \}/)
  assert.match(emailRoute, /<RequireCapability requirement=\{routeAccess\['\/email'\]\}>/)
  assert.match(emailRoute, /const emailDraftAccess = \{ allOf: \[CAPABILITIES\.outboundDraftSave\] \}/)
  assert.match(emailRoute, /const emailSendAccess = \{ allOf: \[CAPABILITIES\.outboundSend\] \}/)
})

test('email workbench is reachable from production IA navigation and command palette', () => {
  assert.match(appShell, /to: '\/email'[\s\S]*label: 'Email'[\s\S]*access: routeAccess\['\/email'\]/)
  assert.match(appShell, /\{ label: '工作台', items: \['\/', '\/webchat', '\/webcall', '\/email'\] \}/)
  assert.match(commandPalette, /id: 'email-workbench'[\s\S]*label: '打开 Email'[\s\S]*to: '\/email'[\s\S]*access: routeAccess\['\/email'\]/)
})

test('email queue uses backend mailbox projection instead of frontend ticket filtering', () => {
  assert.match(types, /export interface EmailMailboxQueueItem/)
  assert.match(types, /export interface EmailMailboxQueueResponse/)
  assert.match(apiClient, /emailMailboxQueue: \(params\?: CaseQueryParams\) => request<EmailMailboxQueueResponse>/)
  assert.match(emailRoute, /queryFn: \(\) => api\.emailMailboxQueue\(\{ q: query \|\| undefined, status: status \|\| undefined \}\)/)
  assert.match(emailRoute, /queueReasonTone\(item\.queue_reason\)/)
  assert.match(emailRoute, /EmailQueueCard/)
  assert.doesNotMatch(emailRoute, /function isEmailCandidate/)
  assert.doesNotMatch(emailRoute, /EMAIL_QUEUE_TOKENS/)
  assert.doesNotMatch(emailRoute, /api\.cases\(\{ q: query/)
})

test('email agent workbench excludes runtime/admin diagnostics from the primary agent page', () => {
  for (const forbidden of [
    'EmailMailboxDaemon',
    'EmailInboundSync',
    'EmailDeliveryReceiptRecorder',
    'emailRetryAccess',
    'emailInboundSyncAccess',
    'emailDeliveryReceiptAccess',
    'emailMailboxSyncAccess',
    'Mailbox Polling / IMAP Daemon',
    'Delivery Receipt / Provider Event',
    'Inbound Sync / Audit',
    'window.confirm',
    'api.requeueOutboundMessage',
    'api.recordEmailDeliveryReceipt',
    'api.ingestInboundEmail',
    'api.emailMailboxSyncStatus',
  ]) {
    assert.doesNotMatch(emailRoute, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.match(emailRoute, /一线客服只处理邮件队列、邮件线程、回复草稿和发送确认/)
})

test('email workbench closes draft save, outbound send, attachment, and timeline refresh loops', () => {
  assert.match(apiClient, /saveOutboundDraft: \(ticketId: number, payload: OutboundSendPayload\)/)
  assert.match(apiClient, /sendOutboundMessage: \(ticketId: number, payload: OutboundSendPayload\)/)
  assert.match(apiClient, /uploadTicketAttachment: \(ticketId: number, file: File, visibility = 'external'\)/)
  assert.match(emailRoute, /api\.saveOutboundDraft\(activeCase\.id, \{ channel: 'email', subject: subject\.trim\(\), body: body\.trim\(\), attachment_ids: attachmentIds \}\)/)
  assert.match(emailRoute, /api\.sendOutboundMessage\(activeCase\.id, \{ channel: 'email', subject: subject\.trim\(\), body: body\.trim\(\), attachment_ids: attachmentIds \}\)/)
  assert.match(emailRoute, /api\.ticketOutboundChannelCapabilities\(activeCase\.id\)/)
  assert.match(emailRoute, /api\.ticketTimeline\(selectedTicketId as number, \{ limit: 30 \}\)/)
  assert.match(emailRoute, /api\.uploadTicketAttachment\(activeCase\.id, file, 'external'\)/)
  assert.match(emailRoute, /client\.invalidateQueries\(\{ queryKey: \['ticketTimeline', activeCase\.id\] \}\)/)
  assert.doesNotMatch(emailRoute, /\bfetch\s*\(/)
})

test('email composer uses final send confirmation instead of a fragile checkbox or window confirm', () => {
  assert.match(emailRoute, /const \[confirmSend, setConfirmSend\] = useState\(false\)/)
  assert.match(emailRoute, /<ConfirmDialog/)
  assert.match(emailRoute, /title="确认发送外部 Email？"/)
  assert.match(emailRoute, /confirmLabel="确认发送 Email"/)
  assert.match(emailRoute, /setConfirmSend\(true\)/)
  assert.doesNotMatch(emailRoute, /const \[confirmExternal, setConfirmExternal\]/)
  assert.doesNotMatch(emailRoute, /window\.confirm/)
})

test('email composer binds ticket attachments to outbound draft and send payloads', () => {
  assert.match(types, /attachment_ids\?: number\[\]/)
  assert.match(emailRoute, /const \[attachmentIds, setAttachmentIds\] = useState<number\[\]>\(\[\]\)/)
  assert.match(emailRoute, /activeCase\.attachments \?\? \[\]/)
  assert.match(emailRoute, /supports_attachments/)
  assert.match(emailRoute, /function toggleAttachment\(attachmentId: number\)/)
  assert.match(emailRoute, /const MAX_EMAIL_ATTACHMENTS = 10/)
  assert.match(emailRoute, /files\.length > MAX_EMAIL_ATTACHMENTS - attachmentIds\.length/)
  assert.doesNotMatch(emailRoute, /availableAttachments\.slice\(0, 8\)/)
})
