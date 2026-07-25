import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const read = (path) => fs.readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')
const format = read('src/lib/format.ts')
const casePane = read('src/features/operator-workspace/OperatorWorkspaceCase.tsx')
const closure = read('src/features/operator-workspace/OperatorWorkspaceClosure.tsx')
const conversation = read('src/features/operator-workspace/OperatorWorkspaceConversation.tsx')
const actions = read('src/features/operator-workspace/OperatorWorkspaceActions.tsx')
const navigation = read('src/app/navigation.ts')
const shell = read('src/app/AppShell.tsx')
const theme = read('src/theme/nexusTheme.ts')

test('free text remains verbatim and exact enum translation cannot mutate substrings', () => {
  assert.match(format, /export function displayVerbatimText/)
  assert.match(format, /displayTextLabels\[source\.trim\(\)\.toLowerCase\(\)\] \|\| source/)
  assert.doesNotMatch(format, /textReplacements|text\.replace\(pattern/)
  assert.match(conversation, /displayVerbatimText\(message\.body_text \|\| message\.body/)
  assert.doesNotMatch(conversation, /sanitizeDisplayText\(message\.body_text/)
})

test('case spine and close command consume the one server closure receipt', () => {
  assert.match(casePane, /useTicketClosureReadiness/)
  assert.match(casePane, /receipt\.readiness\.notification_satisfied/)
  assert.match(casePane, /const readiness = receipt\.readiness/)
  assert.match(casePane, /sourceClosed && readiness\.closure_ready/)
  assert.doesNotMatch(casePane, /item\.source_status === 'closed' \? '已安全关闭'/)
  assert.match(closure, /const latest = await supportApi\.ticketClosureReadiness/)
  assert.match(closure, /确认安全关闭工单/)
})

test('capacity-releasing conversation close requires explicit result and review', () => {
  assert.match(actions, /useState<ConversationOutcome \| ''>\(''\)/)
  assert.match(actions, /人工在线解决需要填写至少 10 个字的处理说明/)
  assert.match(actions, /核对结束信息/)
  assert.match(actions, /确认结束当前会话/)
  assert.doesNotMatch(actions, /useState<ConversationOutcome>\('human_resolved'\)/)
})

test('authenticated route titles and interaction targets have one authority', () => {
  assert.match(navigation, /APP_ROUTE_TITLES/)
  assert.match(shell, /document\.title = APP_ROUTE_TITLES\[activeRoute\]/)
  assert.match(shell, /显示时区/)
  assert.match(theme, /const focusOutline = '3px solid #124AA8'/)
  assert.match(theme, /sizeSmall: \{ minHeight: 44/)
  assert.match(theme, /MuiListItemButton/)
  assert.match(theme, /MuiSwitch/)
})

test('dynamic announcements are bounded and the message timeline is not a live region', () => {
  assert.match(conversation, /role="log"/)
  assert.match(conversation, /aria-live="off"/)
  assert.match(conversation, /收到 \$\{newMessageCount\} 条新消息/)
})
