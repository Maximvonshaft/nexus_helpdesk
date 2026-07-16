import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const statusPath = resolve(root, 'src/lib/supportStatus.ts')
const supportStatus = existsSync(statusPath) ? read('src/lib/supportStatus.ts') : ''
const workspacePage = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
const workspaceQueue = read('src/features/operator-workspace/OperatorWorkspaceQueue.tsx')
const workspaceCase = read('src/features/operator-workspace/OperatorWorkspaceCase.tsx')
const workspaceConversation = read('src/features/operator-workspace/OperatorWorkspaceConversation.tsx')
const workspaceState = read('src/features/operator-workspace/operatorWorkspaceState.ts')
const workspace = [workspacePage, workspaceQueue, workspaceCase, workspaceConversation, workspaceState].join('\n')
const workspaceApi = read('src/lib/operatorWorkspaceApi.ts')
const workspacePresentation = read('src/lib/operatorWorkspacePresentation.ts')
const operationalPresentation = read('src/domain/operationalPresentation.ts')
const channels = read('src/features/channels/ChannelsPage.tsx')
const runtime = read('src/features/runtime/RuntimePage.tsx')
const theme = read('src/theme/nexusTheme.ts')

test('frontend operational health uses an explicit fail-closed mapping module', () => {
  assert.equal(existsSync(statusPath), true)
  assert.match(supportStatus, /export function healthPresentation/)
  for (const state of ['disconnected', 'offline', 'reconnecting', 'unknown']) assert.match(supportStatus, new RegExp(state))
  assert.doesNotMatch(supportStatus, /\.includes\(/)
  assert.match(channels, /healthPresentation/)
})

test('source state and ownership never claim business success', () => {
  assert.match(workspacePresentation, /sourceStatusPresentation/)
  assert.match(workspacePresentation, /ownerPresentation/)
  assert.match(workspacePresentation, /来源已解决/)
  assert.match(workspacePresentation, /来源已关闭/)
  assert.doesNotMatch(workspacePresentation, /业务已完成|安全结案/)
  assert.doesNotMatch(workspace, /handoff_status === 'accepted'\) return 'success'/)
})

test('controlled actions distinguish request acceptance from verified outcome', () => {
  assert.match(workspacePresentation, /outcomePresentation/)
  for (const state of ['queued', 'submitted', 'operational_completed', 'business_result_confirmed']) assert.match(operationalPresentation, new RegExp(state))
  assert.match(workspacePage, /处理编号/)
  assert.match(workspacePage, /可以申请取消|当前不可取消/)
  assert.match(workspacePage, /修改运单、电话或原因后需重新检查/)
  assert.match(operationalPresentation, /请求已排队/)
  assert.doesNotMatch(workspacePage, /Job #|技术追踪标识|预检不是取消完成/)
})

test('runtime header cannot show normal while loading, unavailable, or not ok', () => {
  assert.match(supportStatus, /runtimePresentation/)
  assert.match(runtime, /runtimePresentation\(/)
  assert.match(runtime, /runtime\.isLoading/)
  assert.match(runtime, /runtime\.isError/)
  assert.doesNotMatch(runtime, /warnings\?\.length \? '需要关注' : '正常'/)
})

test('canonical MUI surfaces meet bounded accessibility truth requirements', () => {
  assert.match(theme, /MuiButton:/)
  assert.match(theme, /minHeight:\s*44/)
  assert.match(theme, /focus-visible/)
  assert.match(theme, /prefers-reduced-motion/)
  assert.match(workspaceConversation, /aria-live="polite"/)
  assert.match(workspaceCase, /aria-label="处理进度"/)
  assert.match(channels, /<TableHead>/)
  assert.match(channels, /aria-label="当前启用的渠道账号"/)
})

test('workspace queue and conversation freshness remain visible and bounded', () => {
  assert.match(workspacePage, /refetchInterval:\s*15_?000/)
  assert.doesNotMatch(workspace, /refetchInterval:\s*selectedItem\?\.source_links\.conversation\s*\?\s*5_?000/)
  assert.match(workspacePage, /conversationEvents\(/)
  assert.match(workspaceState, /mergeLatestWorkspaceThread/)
  assert.match(workspaceState, /mergeOlderWorkspaceThread/)
  assert.match(workspacePage, /loadOlderMessages/)
  assert.match(workspaceConversation, /加载更早消息/)
  assert.match(workspaceQueue, /加载更多任务/)
  assert.match(workspaceApi, /before_message_id/)
  assert.match(workspaceApi, /\/api\/webchat\/admin\/tickets\/\$\{ticketId\}\/events/)
  assert.doesNotMatch(workspaceApi, /thread-v2|thread-page/)
})
