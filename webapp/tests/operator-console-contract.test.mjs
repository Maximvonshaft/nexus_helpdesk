import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const overviewRoute = readFileSync(resolve(root, 'src/routes/index.tsx'), 'utf8')
const workspaceRoute = readFileSync(resolve(root, 'src/routes/workspace.tsx'), 'utf8')
const runtimeRoute = readFileSync(resolve(root, 'src/routes/runtime.tsx'), 'utf8')
const webchatRoute = readFileSync(resolve(root, 'src/routes/webchat.tsx'), 'utf8')
const webcallOperatorRoute = readFileSync(resolve(root, 'src/routes/webcall-operator.tsx'), 'utf8')
const webchatInboxV5 = readFileSync(resolve(root, 'src/features/webchat-inbox-v5/WebchatInboxV5Page.tsx'), 'utf8')
const webchatVoiceApi = readFileSync(resolve(root, 'src/lib/webchatVoiceApi.ts'), 'utf8')
const agentWebCallPanel = readFileSync(resolve(root, 'src/components/webcall/AgentWebCallPanel.tsx'), 'utf8')
const replyPanel = readFileSync(resolve(root, 'src/components/operator/CustomerReplyPanel.tsx'), 'utf8')
const rbacManifest = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const usersRoute = readFileSync(resolve(root, 'src/routes/users.tsx'), 'utf8')
const speedafActionsPanel = readFileSync(resolve(root, 'src/components/operator/SpeedafActionsPanel.tsx'), 'utf8')

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
  assert.match(replyPanel, /api\.sendOutboundMessage\(activeCase\.id, selectedIsEmail \? \{ channel, subject: subject\.trim\(\), body: body\.trim\(\) \} : \{ channel, body: body\.trim\(\) \}\)/)
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

test('customer reply panel requires explicit email subject for SMTP sends', () => {
  assert.match(types, /export type OutboundSendPayload = \{/)
  assert.match(types, /subject\?: string \| null/)
  assert.match(replyPanel, /const \[subject, setSubject\] = useState\(defaultEmailSubject\(activeCase\)\)/)
  assert.match(replyPanel, /const selectedIsEmail = channel === 'email'/)
  assert.match(replyPanel, /!selectedIsEmail \|\| subject\.trim\(\)/)
  assert.match(replyPanel, /<Field label="Email 主题" required/)
  assert.match(replyPanel, /我确认这是 SMTP 外部邮件发送/)
  assert.match(replyPanel, /Email 收件人/)
})

test('webchat admin events are routed through unified api client', () => {
  assert.match(apiClient, /export type WebchatEventsPage = \{/)
  assert.match(apiClient, /webchatEvents: \(ticketId: number, afterId: number, init\?: RequestInit\)/)
  assert.match(apiClient, /\/api\/webchat\/admin\/tickets\/\$\{ticketId\}\/events/)
  assert.match(webchatRoute, /WebchatInboxV5Page/)
  assert.match(webchatInboxV5, /api\.webchatEvents\(selectedTicketId as number, lastEventId, \{ signal \}\)/)
  assert.doesNotMatch(webchatRoute, /fetch\(/)
  assert.doesNotMatch(webchatInboxV5, /\bfetch\s*\(/)
  assert.doesNotMatch(webchatRoute, /Authorization/)
  assert.doesNotMatch(webchatInboxV5, /Authorization/)
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
  assert.match(types, /export interface RequestTraceResult \{/)
  assert.match(apiClient, /requeueJob: \(jobId: number\) => request<RuntimeRecoveryResult>/)
  assert.match(apiClient, /\/api\/admin\/jobs\/\$\{jobId\}\/requeue/)
  assert.match(apiClient, /requeueDeadJobs: \(params\?: \{ job_type\?: string; limit\?: number \}\)/)
  assert.match(apiClient, /\/api\/admin\/jobs\/requeue-dead/)
  assert.match(apiClient, /requeueOutboundMessage: \(messageId: number\) => request<RuntimeRecoveryResult>/)
  assert.match(apiClient, /\/api\/admin\/outbound\/\$\{messageId\}\/requeue/)
  assert.match(apiClient, /requeueDeadOutbound: \(params\?: \{ limit\?: number \}\)/)
  assert.match(apiClient, /\/api\/admin\/outbound\/requeue-dead/)
  assert.match(apiClient, /requestTrace: \(requestId: string\) => request<RequestTraceResult>/)
  assert.match(apiClient, /\/api\/admin\/provider-runtime\/request-trace\/\$\{encodeURIComponent\(requestId\)\}/)
})

test('runtime page exposes confirmed recovery actions and refreshes runtime views', () => {
  assert.match(runtimeRoute, /data-testid="runtime-recovery-actions"/)
  assert.match(runtimeRoute, /<ConfirmDialog/)
  assert.doesNotMatch(runtimeRoute, /window\.confirm/)
  assert.match(runtimeRoute, /api\.requeueDeadJobs\(\{ limit: 50 \}\)/)
  assert.match(runtimeRoute, /api\.requeueDeadOutbound\(\{ limit: 50 \}\)/)
  assert.match(runtimeRoute, /api\.requeueJob\(job\.id\)/)
  assert.match(runtimeRoute, /重排 dead 后台任务/)
  assert.match(runtimeRoute, /重排 dead outbound/)
  assert.match(runtimeRoute, /重排此任务/)
  assert.match(runtimeRoute, /不会删除任务，不会跳过权限，不会绕过后端审计/)
  assert.match(runtimeRoute, /data-testid="runtime-request-trace"/)
  assert.match(runtimeRoute, /Request Trace Drawer/)
  assert.match(runtimeRoute, /api\.requestTrace\(traceId\)/)
  assert.match(runtimeRoute, /request_id、error_code、retryability、audit 和 timeline/)
  for (const key of ['runtimeHealth', 'readiness', 'signoff', 'jobs', 'queueSummary', 'openclawConnectivity']) {
    assert.match(runtimeRoute, new RegExp(`invalidateQueries\\(\\{ queryKey: \\['${key}'\\] \\}\\)`))
  }
})

test('rbac manifest centralizes route and high-risk action access', () => {
  assert.match(rbacManifest, /export const routeAccess/)
  assert.match(rbacManifest, /export const actionAccess/)
  for (const capability of [
    'tool:speedaf.work_order.create:write',
    'tool:speedaf.order.update_address:write',
    'tool:speedaf.order.cancel:write',
    'webcall.voice.read',
    'webcall.voice.queue.view',
    'webcall.voice.accept',
    'webcall.voice.reject',
    'webcall.voice.end',
  ]) {
    assert.match(rbacManifest, new RegExp(capability.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
})

test('users page uses route guard, grouped capability metadata, and dangerous confirmations', () => {
  assert.match(usersRoute, /<RequireCapability requirement=\{routeAccess\['\/users'\]\}>/)
  assert.match(usersRoute, /capabilityMetadata/)
  assert.match(usersRoute, /isHighRiskCapability/)
  assert.match(usersRoute, /<ConfirmDialog/)
})

test('speedaf and webcall actions are capability gated in the operator UI', () => {
  assert.match(speedafActionsPanel, /canCreateSpeedafWorkOrder/)
  assert.match(speedafActionsPanel, /canUpdateSpeedafAddress/)
  assert.match(speedafActionsPanel, /canCancelSpeedafOrder/)
  assert.match(speedafActionsPanel, /<ConfirmDialog/)
  assert.match(agentWebCallPanel, /canAcceptWebcallVoice/)
  assert.match(agentWebCallPanel, /canRejectWebcallVoice/)
  assert.match(agentWebCallPanel, /canEndWebcallVoice/)
})

test('operator navigation uses workflow-oriented entrypoints', () => {
  assert.match(appShell, /data-testid="operator-primary-navigation"/)
  assert.match(appShell, /处理工单/)
  assert.match(appShell, /WebChat 收件箱/)
  assert.match(appShell, /WebCall 工作台/)
  assert.match(appShell, /运行恢复/)
  assert.match(appShell, /dead\/requeue 自助处理/)
  assert.match(appShell, /runtimeNeedsAttention/)
  assert.match(appShell, /需处理 \{runtimeAttentionCount\}/)
  assert.match(appShell, /运行需处理/)
})

test('command palette exposes high-frequency operator workflow shortcuts', () => {
  assert.match(commandPalette, /data-testid="operator-command-palette-actions"/)
  assert.match(commandPalette, /处理工单 \/ 客户回复/)
  assert.match(commandPalette, /打开 WebChat 收件箱/)
  assert.match(commandPalette, /打开 WebCall 工作台/)
  assert.match(commandPalette, /进入运行恢复 \/ dead 重排/)
  assert.match(commandPalette, /按 request_id 排障/)
  assert.match(commandPalette, /刷新运行状态/)
  assert.match(commandPalette, /queryClient\.invalidateQueries\(\{ queryKey: \['runtimeHealth'\] \}\)/)
  assert.match(commandPalette, /queryClient\.invalidateQueries\(\{ queryKey: \['queueSummary'\] \}\)/)
  assert.match(commandPalette, /navigate\(\{ to: '\/runtime' \}\)/)
})

test('overview page provides priority action entrypoints', () => {
  assert.match(overviewRoute, /data-testid="overview-priority-actions"/)
  assert.match(overviewRoute, /处理客户工单/)
  assert.match(overviewRoute, /打开工单处理/)
  assert.match(overviewRoute, /查看 WebChat 来信/)
  assert.match(overviewRoute, /打开 WebChat 收件箱/)
  assert.match(overviewRoute, /运行恢复待处理/)
  assert.match(overviewRoute, /打开运行恢复/)
  assert.match(overviewRoute, /needsRuntimeRecovery/)
  assert.match(overviewRoute, /navigate\(\{ to: '\/workspace' \}\)/)
  assert.match(overviewRoute, /navigate\(\{ to: '\/webchat' \}\)/)
  assert.match(overviewRoute, /navigate\(\{ to: '\/runtime' \}\)/)
})

test('admin operator surfaces do not bypass unified api client with raw fetch', () => {
  const checkedFiles = [
    ['src/routes/workspace.tsx', workspaceRoute],
    ['src/routes/runtime.tsx', runtimeRoute],
    ['src/routes/webchat.tsx', webchatRoute],
    ['src/routes/webcall-operator.tsx', webcallOperatorRoute],
    ['src/features/webchat-inbox-v5/WebchatInboxV5Page.tsx', webchatInboxV5],
    ['src/routes/index.tsx', overviewRoute],
    ['src/layouts/AppShell.tsx', appShell],
    ['src/components/ui/CommandPalette.tsx', commandPalette],
    ['src/components/operator/CustomerReplyPanel.tsx', replyPanel],
    ['src/components/webcall/AgentWebCallPanel.tsx', agentWebCallPanel],
    ['src/lib/webchatVoiceApi.ts', webchatVoiceApi],
  ]
  const offenders = checkedFiles
    .filter(([, text]) => /\bfetch\s*\(/.test(text))
    .map(([name]) => name)
  assert.deepEqual(offenders, [])
})


test('webcall accept flow uses single microphone acquisition before accept and reuse for publish', () => {
  const micRequestIndex = agentWebCallPanel.indexOf("setCallState('requesting_mic')")
  const acceptIndex = agentWebCallPanel.indexOf('webchatVoiceApi.acceptSession')
  const publishIndex = agentWebCallPanel.indexOf('publishTrack(audioTrack)')
  assert.ok(micRequestIndex >= 0)
  assert.ok(acceptIndex > micRequestIndex)
  assert.ok(publishIndex > acceptIndex)
  assert.match(agentWebCallPanel, /localAudioRef\.current = audioTrack/)
  assert.doesNotMatch(agentWebCallPanel, /navigator\.mediaDevices\.getUserMedia/)
  assert.doesNotMatch(agentWebCallPanel, /stop\(\)[\s\S]{0,300}createLocalAudioTrack/)
})

test('webcall accept failure paths clean up media resources', () => {
  assert.match(agentWebCallPanel, /onError: async \(err: unknown\) => \{\s*await cleanupRoom\(\)/)
  assert.match(agentWebCallPanel, /room\.localParticipant\.publishTrack\(audioTrack\)/)
  assert.match(agentWebCallPanel, /if \(localAudioRef\.current\) \{\s*localAudioRef\.current\.stop\(\)/)
  assert.match(agentWebCallPanel, /if \(roomRef\.current\) \{\s*roomRef\.current\.disconnect\(\)/)
})

test('webcall non-livekit accept path cleans up acquired microphone track before return', () => {
  const nonLiveKitBranch = agentWebCallPanel.match(/if \(accepted\.provider !== 'livekit'\) \{[\s\S]*?return accepted\n\s*\}/)?.[0] ?? ''
  assert.ok(nonLiveKitBranch)
  const cleanupIndex = nonLiveKitBranch.indexOf('await cleanupRoom()')
  const returnIndex = nonLiveKitBranch.indexOf('return accepted')
  assert.ok(cleanupIndex >= 0)
  assert.ok(returnIndex > cleanupIndex)
})
