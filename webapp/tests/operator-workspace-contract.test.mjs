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
const workspaceCss = readIfPresent('src/features/operator-workspace/operator-workspace.css')
const workspaceApi = readIfPresent('src/lib/operatorWorkspaceApi.ts')
const workspaceTypes = readIfPresent('src/lib/operatorWorkspaceTypes.ts')
const workspacePresentation = readIfPresent('src/lib/operatorWorkspacePresentation.ts')
const operationalPresentation = readIfPresent('src/domain/operationalPresentation.ts')
const appShell = readIfPresent('src/app/AppShell.tsx')
const navigation = readIfPresent('src/app/navigation.ts')


test('workspace is the canonical authenticated operator route', () => {
  assert.equal(existsSync(resolve(root, 'src/routes/workspace.tsx')), true)
  assert.equal(existsSync(resolve(root, 'src/features/operator-workspace/OperatorWorkspacePage.tsx')), true)
  assert.match(router, /WorkspaceRoute/)
  assert.match(router, /WorkspaceRoute,/)
  assert.match(indexRoute, /to:\s*'\/workspace'/)
  assert.match(workspaceRoute, /path:\s*'\/workspace'/)
  assert.match(workspaceRoute, /getSupportToken/)
})


test('workspace consumes the canonical unified queue with explicit scope', () => {
  assert.equal(existsSync(resolve(root, 'src/lib/operatorWorkspaceApi.ts')), true)
  assert.equal(existsSync(resolve(root, 'src/lib/operatorWorkspaceTypes.ts')), true)
  assert.match(workspaceApi, /\/api\/admin\/operator-queue\/unified/)
  assert.match(workspaceApi, /X-Nexus-Tenant/)
  assert.match(workspaceApi, /country_code/)
  assert.match(workspaceApi, /channel_key/)
  assert.match(workspaceApi, /cursor/)
  assert.match(workspaceTypes, /source_type:\s*'handoff'\s*\|\s*'ticket'\s*\|\s*'dispatch'/)
  assert.match(workspaceTypes, /next_cursor:\s*string\s*\|\s*null/)
  assert.match(appShell, /工作范围/)
  assert.match(appShell, /country_code/)
  assert.match(appShell, /channel_key/)
  assert.match(workspaceRoute, /authorizedScopes/)
})


test('primary navigation is capability-derived and separates system administration', () => {
  assert.match(navigation, /operator_queue\.read/)
  assert.match(navigation, /ai_config\.read/)
  assert.match(navigation, /channel_account\.manage/)
  assert.match(navigation, /runtime\.manage/)
  assert.match(navigation, /工作台/)
  assert.match(navigation, /知识/)
  assert.match(navigation, /渠道/)
  assert.match(navigation, /运行与审计/)
})


test('case spine and closure blocker keep technical state separate from business closure', () => {
  for (const authority of ['CaseHeader', 'EvidencePanel', 'ConversationPanel', 'ActionPanel']) {
    assert.match(workspacePage, new RegExp(authority))
  }
  assert.match(workspacePage, /前端建议不替代服务端权限、政策和结果权威/)
  assert.match(operationalPresentation, /技术成功不等于运营完成、客户通知或安全结案/)
  assert.doesNotMatch(workspacePage, />已结束</)
  assert.doesNotMatch(workspacePage, />处理成功</)
})


test('evidence classes and action outcomes are explicit and fail closed', () => {
  assert.equal(existsSync(resolve(root, 'src/lib/operatorWorkspacePresentation.ts')), true)
  for (const label of ['事实与依据', '客户主张', '知识与政策', 'AI 建议', '人工决定', '系统事件', '动作结果', '客户通知回执']) {
    assert.match(workspacePresentation + workspacePage, new RegExp(label))
  }
  for (const label of ['请求已排队', '技术处理完成', '运营已完成', '客户通知已确认', '业务结果已确认', '需要修复']) {
    assert.match(operationalPresentation + workspacePage, new RegExp(label))
  }
  assert.match(operationalPresentation, /business_result_confirmed/)
  assert.match(operationalPresentation, /operational_completed/)
  assert.match(operationalPresentation, /repair_required/)
})


test('actions explain prerequisites and never use disabled buttons as the only explanation', () => {
  assert.match(workspacePage, /不可执行原因/)
  assert.match(workspacePage, /缺少运单/)
  assert.match(workspacePage, /缺少客户电话/)
  assert.match(workspacePage, /当前案例没有可用会话/)
  assert.match(workspacePage, /当前权限不允许/)
})


test('conversation messages expose delivery state rather than equating local display with delivery', () => {
  assert.match(workspacePage, /messageDeliveryPresentation/)
  for (const state of ['queued', 'sent', 'delivered', 'failed']) {
    assert.match(workspacePresentation, new RegExp(state))
  }
  assert.match(workspacePage, /送达状态/)
})


test('responsive structure keeps queue, case, communication and actions reachable', () => {
  assert.equal(existsSync(resolve(root, 'src/features/operator-workspace/operator-workspace.css')), true)
  for (const view of ['queue', 'case', 'conversation', 'actions']) {
    assert.match(workspacePage, new RegExp(`'${view}'`))
  }
  assert.match(workspacePage, /队列/)
  assert.match(workspacePage, /案例/)
  assert.match(workspacePage, /沟通/)
  assert.match(workspacePage, /动作/)
  assert.match(workspaceCss, /@media\s*\(max-width:\s*980px\)/)
  assert.match(workspaceCss, /@media\s*\(max-width:\s*640px\)/)
  assert.match(workspaceCss, /100dvh/)
  assert.doesNotMatch(workspaceCss, /operator-(?:context|actions)[^{]*\{[^}]*display:\s*none/)
})


test('workspace preserves scroll ownership, protects drafts, and transfers mobile focus', () => {
  assert.match(workspacePage, /useLayoutEffect/)
  assert.match(workspacePage, /isNearMessageBottom/)
  assert.match(workspacePage, /newMessageCount/)
  assert.match(workspacePage, /条新消息/)
  assert.match(workspacePage, /beforeunload/)
  assert.match(workspacePage, /onReplyDirtyChange/)
  assert.match(workspacePage, /放弃未发送的回复/)
  assert.match(workspacePage, /focus\(\{ preventScroll: true \}\)/)
  assert.match(workspacePage, /tabIndex=\{-1\}/)
  assert.doesNotMatch(workspacePage, /messagesRef\.current\.scrollTop\s*=\s*messagesRef\.current\.scrollHeight/)
})

test('workspace resolves legacy session deep links under canonical scope', () => {
  assert.match(workspacePage, /operatorWorkspaceSessionDeepLink/)
  assert.match(workspacePage, /supportApi\.supportConversationDetail/)
  assert.match(workspacePage, /requestedQueueItem/)
  assert.match(workspacePage, /queue\.fetchNextPage/)
  assert.match(workspacePage, /url\.searchParams\.delete\('session'\)/)
})

test('workspace keeps a dirty reply attached when polling removes the selected queue item', () => {
  assert.match(workspacePage, /retainedSelectedItem/)
  assert.match(workspacePage, /preserveMissingSelection/)
  assert.match(workspacePage, /replyDraftDirty\s*&&\s*selectedQueueItemMissing|selectedQueueItemMissing\s*&&\s*replyDraftDirty/)
  assert.match(workspacePage, /!replyDraftDirty/)
  assert.match(workspacePage, /当前任务已离开队列，回复草稿仍保留/)
  assert.match(workspacePage, /selectionUnavailable/)
  assert.match(workspacePage, /当前任务动作已暂停/)
})
