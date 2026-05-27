import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const route = read('src/routes/webchat.tsx')
const inbox = read('src/features/webchat-inbox-v5/WebchatInboxV5Page.tsx')
const apiClient = read('src/lib/api.ts')
const access = read('src/lib/access.ts')
const rbac = read('src/lib/rbac.ts')

test('webchat route replaces the legacy inbox with the V5 production inbox', () => {
  assert.match(route, /WebchatInboxV5Page/)
  assert.match(route, /webchat-inbox-v5\.css/)
  assert.doesNotMatch(route, /SegmentedControl/)
})

test('webchat inbox V5 is wired to real APIs, realtime, fallback polling, evidence, and WebCall', () => {
  for (const contract of [
    /api\.webchatConversations/,
    /api\.webchatHandoffQueue/,
    /api\.webchatThread/,
    /api\.webchatEvents/,
    /api\.webchatReply/,
    /api\.webchatAcceptHandoff/,
    /api\.webchatDeclineHandoff/,
    /api\.webchatForceTakeover/,
    /api\.webchatReleaseHandoff/,
    /api\.webchatResumeAi/,
    /api\.caseDetail/,
    /api\.uploadTicketAttachment/,
    /api\.escalateTicket/,
    /api\.webchatReadState/,
    /useWebchatRealtime/,
    /AgentWebCallPanel/,
    /realtime\.connected \? false : backoffMs/,
  ]) {
    assert.match(inbox, contract)
  }
})

test('webchat inbox V5 has no visible backend-placeholder copy for designed controls', () => {
  for (const forbidden of ['待接口', '后续接', '生产占位', 'agent_to_visitor', '附件发送仍待后端能力', '当前 main 尚未提供']) {
    assert.doesNotMatch(inbox, new RegExp(forbidden))
  }
})

test('ticket attachment and escalation actions go through unified api client and permission helpers', () => {
  assert.match(apiClient, /uploadTicketAttachment: \(ticketId: number, file: File, visibility = 'external'\)/)
  assert.match(apiClient, /\/api\/tickets\/\$\{ticketId\}\/attachments/)
  assert.match(apiClient, /escalateTicket: \(ticketId: number, payload: \{ team_id: number; note: string \}\)/)
  assert.match(apiClient, /\/api\/tickets\/\$\{ticketId\}\/escalate/)
  assert.match(apiClient, /webchatReadState: \(ticketId: number, payload: \{ marked_unread: boolean \}\)/)
  assert.match(apiClient, /\/api\/webchat\/admin\/tickets\/\$\{ticketId\}\/read-state/)
  assert.match(rbac, /uploadAttachment: \{ allOf: \[CAPABILITIES\.attachmentUpload\] \}/)
  assert.match(rbac, /escalateTicket: \{ allOf: \[CAPABILITIES\.ticketEscalate\] \}/)
  assert.match(access, /canUploadAttachment/)
  assert.match(access, /canEscalateTickets/)
})
