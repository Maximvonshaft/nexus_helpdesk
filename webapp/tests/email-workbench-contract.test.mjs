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

test('email workbench route uses RBAC and is reachable from IA', () => {
  assert.ok(router.includes('EmailRoute'))
  assert.ok(router.includes('@/routes/email'))
  assert.ok(emailRoute.includes("path: '/email'"))
  assert.ok(rbac.includes("'/email': { allOf: [CAPABILITIES.ticketRead], anyOf: [CAPABILITIES.outboundDraftSave, CAPABILITIES.outboundSend] }"))
  assert.ok(emailRoute.includes("<RequireCapability requirement={routeAccess['/email']}>"))
  assert.ok(appShell.includes("to: '/email'"))
  assert.ok(commandPalette.includes("id: 'email-workbench'"))
})

test('email workbench uses backend mailbox projection and ticket timeline', () => {
  assert.ok(types.includes('export interface EmailMailboxQueueItem'))
  assert.ok(types.includes('export interface EmailMailboxQueueResponse'))
  assert.ok(apiClient.includes('emailMailboxQueue: (params?: CaseQueryParams) => request<EmailMailboxQueueResponse>'))
  assert.ok(emailRoute.includes("api.emailMailboxQueue({ q: query || undefined, status: status || undefined })"))
  assert.ok(emailRoute.includes('api.caseDetail(selectedTicketId as number)'))
  assert.ok(emailRoute.includes('api.ticketTimeline(selectedTicketId as number, { limit: 30 })'))
  assert.ok(!emailRoute.includes('api.cases({ q: query'))
})

test('email page is agent focused and closes draft/send loops', () => {
  assert.ok(emailRoute.includes('一线客服只处理邮件队列、邮件线程、回复草稿和发送确认'))
  assert.ok(emailRoute.includes('api.saveOutboundDraft(activeCase.id'))
  assert.ok(emailRoute.includes('api.sendOutboundMessage(activeCase.id'))
  assert.ok(emailRoute.includes('api.ticketOutboundChannelCapabilities(activeCase.id)'))
  assert.ok(emailRoute.includes("invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] })"))
  assert.ok(!emailRoute.includes('EmailMailboxDaemon'))
  assert.ok(!emailRoute.includes('EmailInboundSync'))
  assert.ok(!emailRoute.includes('EmailDeliveryReceiptRecorder'))
})

test('email composer uses final confirmation before external send', () => {
  assert.ok(emailRoute.includes('confirmSend'))
  assert.ok(emailRoute.includes('<ConfirmDialog'))
  assert.ok(emailRoute.includes('确认发送外部 Email？'))
  assert.ok(emailRoute.includes('确认发送 Email'))
  assert.ok(!emailRoute.includes('window.confirm'))
})
