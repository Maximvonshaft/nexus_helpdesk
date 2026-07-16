import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const readIfPresent = (path) => {
  const absolute = resolve(root, path)
  return existsSync(absolute) ? readFileSync(absolute, 'utf8') : ''
}

const router = readIfPresent('src/router.tsx')
const indexRoute = readIfPresent('src/routes/index.tsx')
const workspaceRoute = readIfPresent('src/routes/workspace.tsx')
const workspacePage = readIfPresent('src/features/operator-workspace/OperatorWorkspacePage.tsx')
const workspaceApi = readIfPresent('src/lib/operatorWorkspaceApi.ts')
const workspaceTypes = readIfPresent('src/lib/operatorWorkspaceTypes.ts')
const workspacePresentation = readIfPresent('src/lib/operatorWorkspacePresentation.ts')
const operationalPresentation = readIfPresent('src/domain/operationalPresentation.ts')
const appShell = readIfPresent('src/app/AppShell.tsx')
const navigation = readIfPresent('src/app/navigation.ts')
const theme = readIfPresent('src/theme/nexusTheme.ts')

test('workspace is the canonical authenticated operator route', () => {
  assert.equal(existsSync(resolve(root, 'src/routes/workspace.tsx')), true)
  assert.equal(existsSync(resolve(root, 'src/features/operator-workspace/OperatorWorkspacePage.tsx')), true)
  assert.match(router, /WorkspaceRoute/)
  assert.match(router, /WorkspaceRoute,/)
  assert.match(indexRoute, /to:\s*'\/workspace'/)
  assert.match(workspaceRoute, /path:\s*'\/workspace'/)
  assert.match(workspaceRoute, /getSupportToken/)
})

test('workspace consumes one scoped queue and one bounded thread API', () => {
  assert.match(workspaceApi, /\/api\/admin\/operator-queue\/unified/)
  assert.match(workspaceApi, /X-Nexus-Tenant/)
  assert.match(workspaceApi, /before_message_id/)
  assert.match(workspaceApi, /message_limit/)
  assert.match(workspaceApi, /\/api\/webchat\/admin\/tickets\/\$\{ticketId\}\/events/)
  assert.doesNotMatch(workspaceApi, /thread-v2|thread-page/)
  assert.match(workspaceTypes, /source_type:\s*'handoff'\s*\|\s*'ticket'\s*\|\s*'dispatch'/)
  assert.match(workspaceTypes, /next_cursor:\s*string\s*\|\s*null/)
  assert.match(appShell, /工作范围/)
  assert.match(workspaceRoute, /authorizedScopes/)
})

test('primary navigation uses operator-facing domain names', () => {
  for (const capability of ['operator_queue.read', 'ai_config.read', 'channel_account.manage', 'runtime.manage']) {
    assert.match(navigation, new RegExp(capability.replace('.', '\\.')))
  }
  for (const label of ['案例处理', '知识与流程', '渠道管理', '系统运行', '运营监控']) {
    assert.match(navigation, new RegExp(label))
  }
})

test('processing progress and closure status remain truthful without narrative copy', () => {
  for (const authority of ['CaseHeader', 'CaseSpine', 'EvidencePanel', 'ConversationPanel', 'ActionPanel']) {
    assert.match(workspacePage, new RegExp(authority))
  }
  assert.match(workspacePage, /处理进度/)
  assert.match(workspacePage, /暂无可信结案信息/)
  assert.match(operationalPresentation, /技术成功不等于运营完成、客户通知或安全结案/)
  assert.doesNotMatch(workspacePage, /只显示当前接口已经提供的事实|服务端最终授权|页面提示不替代/)
  assert.doesNotMatch(workspacePage, />处理成功</)
})

test('evidence and outcome labels are operator-facing and explicit', () => {
  for (const label of ['已核实信息', '客户说法', '知识与政策', '自动回复建议', '处理决定', '系统记录', '操作结果', '客户通知']) {
    assert.match(workspacePresentation + workspacePage, new RegExp(label))
  }
  for (const label of ['请求已排队', '技术处理完成', '运营已完成', '客户通知已确认', '业务结果已确认', '需要修复']) {
    assert.match(operationalPresentation + workspacePage, new RegExp(label))
  }
  assert.doesNotMatch(workspacePresentation, /事实与依据|客户主张|AI 建议|人工决定|系统事件|动作结果|客户通知回执/)
})

test('actions state prerequisites and use direct operator verbs', () => {
  for (const copy of ['缺少运单', '缺少客户电话', '无权访问任务队列', '接手任务', '接手处理', '转回待处理', '恢复自动回复', '检查是否可取消', '确认申请取消']) {
    assert.match(workspacePage, new RegExp(copy))
  }
  assert.doesNotMatch(workspacePage, /不可执行原因直接说明|当前不可执行：|案例接管|接管案例|释放案例|恢复 AI/)
})

test('conversation exposes bounded history, event freshness and delivery state', () => {
  assert.match(workspacePage, /mergeLatestThread/)
  assert.match(workspacePage, /mergeOlderThread/)
  assert.match(workspacePage, /conversationEvents/)
  assert.match(workspacePage, /EVENT_IDLE_POLL_MS/)
  assert.match(workspacePage, /EVENT_RETRY_MAX_MS/)
  assert.match(workspacePage, /加载更早消息/)
  assert.match(workspacePage, /loadOlderMessagesPreservingPosition/)
  assert.match(workspacePage, /messageDeliveryPresentation/)
  assert.match(workspacePage, /送达状态/)
  assert.doesNotMatch(workspacePage, /refetchInterval:\s*selectedItem\?\.source_links\.conversation\s*\?\s*5_?000/)
})

test('MUI responsive structure keeps all work regions reachable', () => {
  assert.equal(existsSync(resolve(root, 'src/features/operator-workspace/operator-workspace.css')), false)
  assert.equal(existsSync(resolve(root, 'src/features/operator-workspace/operator-workspace-refinements.css')), false)
  for (const view of ['queue', 'case', 'conversation', 'actions']) assert.match(workspacePage, new RegExp(`'${view}'`))
  for (const label of ['待处理', '任务详情', '客户沟通', '操作']) assert.match(workspacePage, new RegExp(label))
  assert.match(workspacePage, /<Tabs/)
  assert.match(workspacePage, /gridTemplateColumns:\s*\{ xs:/)
  assert.match(theme, /MuiTab:/)
})

test('workspace preserves scroll ownership, drafts and mobile focus', () => {
  assert.match(workspacePage, /useLayoutEffect/)
  assert.match(workspacePage, /isNearMessageBottom/)
  assert.match(workspacePage, /newMessageCount/)
  assert.match(workspacePage, /条新消息/)
  assert.match(workspacePage, /beforeunload/)
  assert.match(workspacePage, /onReplyDirtyChange/)
  assert.match(workspacePage, /放弃未发送的回复/)
  assert.match(workspacePage, /focus\(\{ preventScroll: true \}\)/)
  assert.match(workspacePage, /tabIndex=\{-1\}/)
})

test('workspace resolves legacy session deep links under canonical scope', () => {
  assert.match(workspacePage, /operatorWorkspaceSessionDeepLink/)
  assert.match(workspacePage, /supportApi\.supportConversationDetail/)
  assert.match(workspacePage, /requestedQueueItem/)
  assert.match(workspacePage, /queue\.fetchNextPage/)
  assert.match(workspacePage, /url\.searchParams\.delete\('session'\)/)
})

test('workspace keeps a dirty reply attached when the task leaves the queue', () => {
  assert.match(workspacePage, /retainedSelectedItem/)
  assert.match(workspacePage, /preserveMissingSelection/)
  assert.match(workspacePage, /replyDraftDirty\s*&&\s*selectedQueueItemMissing|selectedQueueItemMissing\s*&&\s*replyDraftDirty/)
  assert.match(workspacePage, /任务已离开待处理列表/)
  assert.match(workspacePage, /回复草稿已保留，操作已暂停/)
  assert.match(workspacePage, /selectionUnavailable/)
})
