import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const paths = {
  page: 'src/features/operator-workspace/OperatorWorkspacePage.tsx',
  queue: 'src/features/operator-workspace/OperatorWorkspaceQueue.tsx',
  case: 'src/features/operator-workspace/OperatorWorkspaceCase.tsx',
  conversation: 'src/features/operator-workspace/OperatorWorkspaceConversation.tsx',
  common: 'src/features/operator-workspace/OperatorWorkspaceCommon.tsx',
  state: 'src/features/operator-workspace/operatorWorkspaceState.ts',
  api: 'src/lib/operatorWorkspaceApi.ts',
}
for (const path of Object.values(paths)) assert.equal(existsSync(resolve(root, path)), true, path)
const source = Object.values(paths).map(read).join('\n')
const page = read(paths.page)
const api = read(paths.api)

test('workspace is one route, one module graph and one API adapter', () => {
  const route = read('src/routes/workspace.tsx')
  assert.match(route, /path:\s*'\/workspace'/)
  assert.match(route, /operator-workspace\/lazy/)
  for (const module of ['./OperatorWorkspaceQueue', './OperatorWorkspaceCase', './OperatorWorkspaceCommon', './operatorWorkspaceState']) assert.match(page, new RegExp(module.replace('/', '\\/')))
  assert.doesNotMatch(source, /workspace-v2|new-workspace|thread-v2|thread-page/)
})

test('workspace preserves scoped queue, paging and event freshness', () => {
  assert.match(api, /\/api\/admin\/operator-queue\/unified/)
  assert.match(api, /X-Nexus-Tenant/)
  assert.match(api, /before_message_id/)
  assert.match(api, /message_limit/)
  assert.match(page, /conversationEvents/)
  assert.match(page, /EVENT_IDLE_POLL_MS/)
  assert.match(page, /EVENT_RETRY_MAX_MS/)
  assert.match(read(paths.state), /mergeLatestWorkspaceThread/)
  assert.match(read(paths.state), /mergeOlderWorkspaceThread/)
  assert.match(read(paths.conversation), /加载更早消息/)
  assert.doesNotMatch(source, /refetchInterval:\s*selectedItem\?\.source_links\.conversation\s*\?\s*5_?000/)
})

test('workspace preserves drafts, deep links and mobile reachability', () => {
  assert.match(page, /beforeunload/)
  assert.match(page, /retainedSelectedItem/)
  assert.match(page, /preserveMissingSelection/)
  assert.match(page, /operatorWorkspaceSessionDeepLink/)
  assert.match(page, /focus\(\{ preventScroll: true \}\)/)
  for (const label of ['待处理', '任务详情', '客户沟通', '操作']) assert.match(source, new RegExp(label))
  assert.match(read(paths.queue), /<Tabs/)
  assert.match(read(paths.conversation), /newMessageCount/)
})

test('workspace keeps truthful progress, action safety and operator language', () => {
  for (const label of ['处理进度', '暂无可信结案信息', '已知信息', '接手处理', '转回待处理', '恢复自动回复', '检查是否可取消', '确认申请取消']) assert.match(source, new RegExp(label))
  assert.match(page, /cancelPreviewFingerprint/)
  assert.match(page, /cancelPreview\.fingerprint !== currentCancelFingerprint/)
  assert.doesNotMatch(source, /案例处理链路|事实与证据|案例接管|接管案例|释放案例|恢复 AI|服务端最终授权/)
})

test('workspace orchestration is bounded and does not reabsorb view responsibilities', () => {
  assert.ok(page.split(/\r?\n/).length <= 800)
  assert.doesNotMatch(page, /function\s+(QueueRow|ConversationPanel|CaseSpine|EvidencePanel|EmptyState|ErrorNotice|LoadingState)\b/)
  assert.doesNotMatch(source, /#[0-9a-f]{3,8}\b|rgba?\(\s*\d/i)
})
