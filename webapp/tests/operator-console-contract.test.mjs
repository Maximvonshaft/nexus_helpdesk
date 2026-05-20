import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const workspaceRoute = readFileSync(resolve(root, 'src/routes/workspace.tsx'), 'utf8')
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
