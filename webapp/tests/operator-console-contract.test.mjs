import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const workspaceRoute = readFileSync(resolve(root, 'src/routes/workspace.tsx'), 'utf8')
const runtimeRoute = readFileSync(resolve(root, 'src/routes/runtime.tsx'), 'utf8')
const webchatRoute = readFileSync(resolve(root, 'src/routes/webchat.tsx'), 'utf8')
const webchatVoiceApi = readFileSync(resolve(root, 'src/lib/webchatVoiceApi.ts'), 'utf8')
const agentWebCallPanel = readFileSync(resolve(root, 'src/components/webcall/AgentWebCallPanel.tsx'), 'utf8')
const replyPanel = readFileSync(resolve(root, 'src/components/operator/CustomerReplyPanel.tsx'), 'utf8')

test('workspace case list uses non-legacy paginated API contract', () => {
  assert.match(types, /export interface CaseListPage \{/)
  assert.match(types, /items: CaseListItem\[\]/)
  assert.match(apiClient, /casesPage: \(params\?: CaseQueryParams\) => request<CaseListPage>/)
  assert.match(apiClient, /cases: async \(params\?: CaseQueryParams\): Promise<CaseListItem\[\]>/)
  assert.doesNotMatch(apiClient, /search\.set\('legacy', 'true'\)/)
  assert.doesNotMatch(apiClient, /\/api\/lite\/cases\?legacy=true/)
})

test('workspace frontend DTO names match stable backend case list fields', () => {
  assert.match(types, /ticket_no\?: string \| null/)
  assert.match(types, /title: string/)
  assert.match(types, /assignee_name\?: string \| null/)
  assert.match(types, /team_name\?: string \| null/)
  assert.match(types, /market_code\?: string \| null/)
  assert.match(types, /updated_at: string/)
  assert.match(types, /overdue\?: boolean/)
})

test('ticket summary evidence contract exposes counts and preview fields', () => {
  assert.match(types, /export interface EvidenceSummary \{/)
  assert.match(types, /attachments_count: number/)
  assert.match(types, /openclaw_transcript_count: number/)
  assert.match(types, /openclaw_attachment_references_count: number/)
  assert.match(types, /active_market_bulletins_count: number/)
  assert.match(types, /evidence_summary\?: EvidenceSummary/)
  assert.match(types, /attachments\?: SystemAttachment\[\]/)
  assert.match(types, /openclaw_attachment_references\?: AttachmentReference\[\]/)
  assert.match(types, /active_market_bulletins\?: Bulletin\[\]/)
})

test('workspace still renders evidence panels from summary previews', () => {
  assert.match(workspaceRoute, /activeCase\.attachments/)
  assert.match(workspaceRoute, /activeCase\.openclaw_attachment_references/)
  assert.match(workspaceRoute, /activeCase\.active_market_bulletins/)
  assert.match(apiClient, /caseDetail: \(ticketId: number\) => request<CaseDetail>\(`\/api\/tickets\/\$\{ticketId\}\/summary`\)/)
})

test('workspace mounts customer reply closure panel', () => {
  assert.match(workspaceRoute, /import \{ CustomerReplyPanel \} from '@\/components\/operator\/CustomerReplyPanel'/)
  assert.match(workspaceRoute, /<CustomerReplyPanel activeCase=\{activeCase\} onToast=\{setToast\} \/>/)
})

test('customer reply panel uses ticket-scoped channel readiness and outbound send', () => {
  assert.match(apiClient, /ticketOutboundChannelCapabilities: \(ticketId: number\) => request<OutboundChannelCapabilitiesResponse>/)
  assert.match(apiClient, /`\/api\/tickets\/\$\{ticketId\}\/outbound\/channels\/capabilities`/)
  assert.match(apiClient, /sendOutboundMessage: \(ticketId: number, payload: OutboundSendPayload\)/)
  assert.match(apiClient, /`\/api\/tickets\/\$\{ticketId\}\/outbound\/send`/)
  assert.match(replyPanel, /api\.ticketOutboundChannelCapabilities\(activeCase\.id\)/)
  assert.match(replyPanel, /api\.sendOutboundMessage\(activeCase\.id, \{ channel, body: body\.trim\(\) \}\)/)
})

test('customer reply panel shows send semantics and refreshes workspace after send', () => {
  assert.match(replyPanel, /selectedCapability\.external_send/)
  assert.match(replyPanel, /confirmExternal/)
  assert.match(replyPanel, /外部客户渠道发送/)
  assert.match(replyPanel, /Local WebChat|本地 WebChat|外部渠道发送/)
  assert.match(replyPanel, /invalidateQueries\(\{ queryKey: \['caseDetail', activeCase\.id\] \}\)/)
  assert.match(replyPanel, /invalidateQueries\(\{ queryKey: \['ticketTimeline', activeCase\.id\] \}\)/)
  assert.match(replyPanel, /invalidateQueries\(\{ queryKey: \['cases'\] \}\)/)
})

test('webchat admin events are routed through unified api client', () => {
  assert.match(apiClient, /export type WebchatEventsPage = \{/)
  assert.match(apiClient, /webchatEvents: \(ticketId: number, afterId: number, init\?: RequestInit\)/)
  assert.match(apiClient, /\/api\/webchat\/admin\/tickets\/\$\{ticketId\}\/events/)
  assert.match(webchatRoute, /api\.webchatEvents\(selectedTicketId as number, lastEventId, \{ signal \}\)/)
  assert.doesNotMatch(webchatRoute, /fetch\(/)
  assert.doesNotMatch(webchatRoute, /Authorization/)
})

test('webchat voice admin calls delegate to unified api client', () => {
  assert.match(apiClient, /webchatVoiceRuntimeConfig: \(init\?: RequestInit\)/)
  assert.match(apiClient, /webchatVoiceSessions: \(ticketId: number, init\?: RequestInit\)/)
  assert.match(apiClient, /webchatVoiceAcceptSession: \(ticketId: number, voiceSessionId: string\)/)
  assert.match(apiClient, /webchatVoiceEndSession: \(ticketId: number, voiceSessionId: string\)/)
  assert.match(webchatVoiceApi, /runtimeConfig: api\.webchatVoiceRuntimeConfig/)
  assert.match(webchatVoiceApi, /listSessions: api\.webchatVoiceSessions/)
  assert.match(webchatVoiceApi, /acceptSession: api\.webchatVoiceAcceptSession/)
  assert.match(webchatVoiceApi, /endSession: api\.webchatVoiceEndSession/)
  assert.doesNotMatch(webchatVoiceApi, /fetch\(/)
  assert.doesNotMatch(webchatVoiceApi, /buildApiUrl/)
  assert.doesNotMatch(webchatVoiceApi, /adminRequest/)
})

test('runtime recovery actions use safe api client endpoints', () => {
  assert.match(apiClient, /export type RuntimeRecoveryResult = \{/)
  assert.match(apiClient, /requeueJob: \(jobId: number\) => request<RuntimeRecoveryResult>/)
  assert.match(apiClient, /\/api\/admin\/jobs\/\$\{jobId\}\/requeue/)
  assert.match(apiClient, /requeueDeadJobs: \(params\?: \{ job_type\?: string; limit\?: number \}\)/)
  assert.match(apiClient, /\/api\/admin\/jobs\/requeue-dead/)
  assert.match(apiClient, /requeueOutboundMessage: \(messageId: number\) => request<RuntimeRecoveryResult>/)
  assert.match(apiClient, /\/api\/admin\/outbound\/\$\{messageId\}\/requeue/)
  assert.match(apiClient, /requeueDeadOutbound: \(params\?: \{ limit\?: number \}\)/)
  assert.match(apiClient, /\/api\/admin\/outbound\/requeue-dead/)
})

test('runtime page exposes confirmed recovery actions and refreshes runtime views', () => {
  assert.match(runtimeRoute, /data-testid="runtime-recovery-actions"/)
  assert.match(runtimeRoute, /window\.confirm/)
  assert.match(runtimeRoute, /api\.requeueDeadJobs\(\{ limit: 50 \}\)/)
  assert.match(runtimeRoute, /api\.requeueDeadOutbound\(\{ limit: 50 \}\)/)
  assert.match(runtimeRoute, /api\.requeueJob\(job\.id\)/)
  assert.match(runtimeRoute, /重排 dead 后台任务/)
  assert.match(runtimeRoute, /重排 dead outbound/)
  assert.match(runtimeRoute, /重排此任务/)
  assert.match(runtimeRoute, /不会删除任务，不会跳过权限，不会绕过后端审计/)
  for (const key of ['runtimeHealth', 'readiness', 'signoff', 'jobs', 'queueSummary', 'openclawConnectivity']) {
    assert.match(runtimeRoute, new RegExp(`invalidateQueries\\(\\{ queryKey: \\['${key}'\\] \\}\\)`))
  }
})

test('admin operator surfaces do not bypass unified api client with raw fetch', () => {
  const checkedFiles = [
    ['src/routes/workspace.tsx', workspaceRoute],
    ['src/routes/runtime.tsx', runtimeRoute],
    ['src/routes/webchat.tsx', webchatRoute],
    ['src/components/operator/CustomerReplyPanel.tsx', replyPanel],
    ['src/components/webcall/AgentWebCallPanel.tsx', agentWebCallPanel],
    ['src/lib/webchatVoiceApi.ts', webchatVoiceApi],
  ]
  const offenders = checkedFiles
    .filter(([, text]) => /\bfetch\s*\(/.test(text))
    .map(([name]) => name)
  assert.deepEqual(offenders, [])
})
