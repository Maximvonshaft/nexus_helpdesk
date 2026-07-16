import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const statusPath = resolve(root, 'src/lib/supportStatus.ts')
const supportStatus = existsSync(statusPath) ? read('src/lib/supportStatus.ts') : ''
const workspace = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
const workspacePresentation = read('src/lib/operatorWorkspacePresentation.ts')
const operationalPresentation = read('src/domain/operationalPresentation.ts')
const channels = read('src/features/channels/ChannelsPage.tsx')
const runtime = read('src/features/runtime/RuntimePage.tsx')
const theme = read('src/theme/nexusTheme.ts')

test('frontend operational health uses an explicit fail-closed mapping module', () => {
  assert.equal(existsSync(statusPath), true, 'src/lib/supportStatus.ts must exist')
  assert.match(supportStatus, /export function healthPresentation/)
  assert.match(supportStatus, /disconnected/)
  assert.match(supportStatus, /offline/)
  assert.match(supportStatus, /reconnecting/)
  assert.match(supportStatus, /unknown/)
  assert.doesNotMatch(supportStatus, /\.includes\(/)
  assert.match(channels, /healthPresentation/)
  assert.doesNotMatch(channels, /function toneForHealth/)
})

test('source state and ownership never claim business success', () => {
  assert.match(workspacePresentation, /sourceStatusPresentation/)
  assert.match(workspacePresentation, /来源状态/)
  assert.match(workspacePresentation, /ownerPresentation/)
  assert.doesNotMatch(workspace, /已结束/)
  assert.doesNotMatch(workspace, /handoff_status === 'accepted'\) return 'success'/)
  assert.doesNotMatch(workspace, /channel === 'whatsapp'\) return 'success'/)
})

test('controlled actions distinguish request acceptance from verified outcome', () => {
  assert.match(workspacePresentation, /outcomePresentation/)
  assert.match(operationalPresentation, /queued/)
  assert.match(operationalPresentation, /submitted/)
  assert.match(operationalPresentation, /operational_completed/)
  assert.match(operationalPresentation, /business_result_confirmed/)
  assert.match(workspace, /技术追踪标识/)
  assert.match(workspace, /预检不是取消完成/)
  assert.match(operationalPresentation, /请求已排队/)
  assert.doesNotMatch(workspace, /<small>Job #\{actionResult\.jobId\}<\/small>/)
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
  assert.match(workspace, /aria-live="polite"/)
  assert.match(workspace, /aria-label="案例处理链路"/)
  assert.match(channels, /<TableHead>/)
  assert.match(channels, /aria-label="当前启用的渠道账号"/)
  assert.doesNotMatch(theme, /#f06423/i)
})

test('workspace queue and selected conversation freshness remain visible and bounded', () => {
  assert.match(workspace, /refetchInterval:\s*15_?000/)
  assert.match(workspace, /refetchInterval: selectedItem\?\.source_links\.conversation \? 5_?000 : false/)
  assert.match(workspace, /刷新中/)
  assert.match(workspace, /加载更多任务/)
})
