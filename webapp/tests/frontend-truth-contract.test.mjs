import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const statusPath = resolve(root, 'src/lib/supportStatus.ts')
const supportStatus = existsSync(statusPath) ? read('src/lib/supportStatus.ts') : ''
const supportConsole = read('src/features/support-console/SupportConsolePage.tsx')
const supportCss = read('src/features/support-console/support-console.css')


test('frontend operational health uses an explicit fail-closed mapping module', () => {
  assert.equal(existsSync(statusPath), true, 'src/lib/supportStatus.ts must exist')
  assert.match(supportStatus, /export function healthPresentation/)
  assert.match(supportStatus, /disconnected/)
  assert.match(supportStatus, /offline/)
  assert.match(supportStatus, /reconnecting/)
  assert.match(supportStatus, /unknown/)
  assert.doesNotMatch(supportStatus, /\.includes\(/)
  assert.match(supportConsole, /healthPresentation/)
  assert.doesNotMatch(supportConsole, /function toneForHealth/)
})


test('source ticket state and ownership never claim business success', () => {
  assert.match(supportStatus, /sourceConversationPresentation/)
  assert.match(supportStatus, /来源状态：已解决/)
  assert.match(supportStatus, /来源状态：已关闭/)
  assert.doesNotMatch(supportConsole, /已结束/)
  assert.doesNotMatch(supportConsole, /handoff_status === 'accepted'\) return 'success'/)
  assert.doesNotMatch(supportConsole, /channel === 'whatsapp'\) return 'success'/)
})


test('controlled actions distinguish request acceptance from verified outcome', () => {
  assert.match(supportStatus, /controlledActionPresentation/)
  assert.match(supportStatus, /queued/)
  assert.match(supportStatus, /submitted/)
  assert.match(supportStatus, /等待确认/)
  assert.match(supportStatus, /business_result_confirmed/)
  assert.match(supportStatus, /operational_completed/)
  assert.doesNotMatch(supportStatus, /\n\s*'completed',/)
  assert.doesNotMatch(supportStatus, /\n\s*'succeeded',/)
  assert.doesNotMatch(supportStatus, /\n\s*'confirmed',/)
  assert.match(supportConsole, /controlledActionPresentation/)
  assert.match(supportConsole, /TechnicalDetails/)
  assert.doesNotMatch(supportConsole, /support-action-result success[\s\S]{0,260}actionResult/)
  assert.doesNotMatch(supportConsole, /<small>Job #\{actionResult\.jobId\}<\/small>/)
})


test('runtime header cannot show normal while loading, unavailable, or not ok', () => {
  assert.match(supportStatus, /runtimePresentation/)
  assert.match(supportConsole, /runtimePresentation\(/)
  assert.match(supportConsole, /runtime\.isLoading/)
  assert.match(supportConsole, /runtime\.isError/)
  assert.doesNotMatch(supportConsole, /warnings\?\.length \? '需要关注' : '正常'/)
})


test('current transitional console meets bounded accessibility truth requirements', () => {
  assert.doesNotMatch(supportCss, /#f06423/i)
  assert.match(supportCss, /\.support-top-tabs button[\s\S]*min-height:\s*44px/)
  assert.match(supportCss, /\.support-segments button[\s\S]*min-height:\s*44px/)
  assert.match(supportCss, /\.support-thread-back[\s\S]*min-height:\s*44px/)
  assert.match(supportConsole, /<table className="support-table">/)
  assert.match(supportConsole, /<th scope="col">/)
  assert.match(supportConsole, /aria-live="polite"/)
})


test('conversation state polling and visible freshness remain aligned', () => {
  assert.match(
    supportConsole,
    /queryKey: \['supportConversationState'\][\s\S]{0,180}enabled: activeView === 'conversations'/,
  )
  assert.match(supportConsole, /会话状态暂停刷新/)
})
