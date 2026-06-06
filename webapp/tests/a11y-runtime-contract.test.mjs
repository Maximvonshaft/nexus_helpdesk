import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const runtime = read('src/a11yRuntime.ts')
const main = read('src/main.tsx')
const a11yCss = read('src/a11y.css')

test('a11y runtime repair is initialized from the React entrypoint', () => {
  assert.match(main, /import \{ initA11yRuntimeRepair \} from '@\/a11yRuntime'/)
  assert.match(main, /initA11yRuntimeRepair\(\)/)
})

test('a11y runtime downgrades incomplete WebChat listbox semantics to button-list semantics', () => {
  assert.match(runtime, /repairWebchatConversationLists/)
  assert.match(runtime, /\[role="listbox"\]\[aria-label="WebChat conversations"\]/)
  assert.match(runtime, /setAttr\(list, 'role', 'list'\)/)
  assert.match(runtime, /removeAttr\(item, 'role'\)/)
  assert.match(runtime, /removeAttr\(item, 'aria-selected'\)/)
  assert.match(runtime, /aria-pressed/)
  assert.match(runtime, /打开 WebChat 会话/)
})

test('WebCall queue filter semantics are no longer repaired by runtime', () => {
  assert.doesNotMatch(runtime, /repairWebcallQueueFilters/)
  assert.doesNotMatch(runtime, /WebCall Operational Queue tabs/)
  assert.doesNotMatch(runtime, /WebCall Operational Queue filters/)
})

test('a11y runtime intercepts dangerous mobile drawer actions before execution', () => {
  assert.match(runtime, /DANGEROUS_DRAWER_CONFIRMATIONS/)
  assert.match(runtime, /label: '释放回队列'/)
  assert.match(runtime, /label: '恢复 AI'/)
  assert.match(runtime, /interceptDangerousDrawerActions/)
  assert.match(runtime, /closest\('\.v5-context-drawer'\)/)
  assert.match(runtime, /event\.preventDefault\(\)/)
  assert.match(runtime, /event\.stopImmediatePropagation\(\)/)
  assert.match(runtime, /showDangerousActionConfirm/)
  assert.match(runtime, /confirmedDangerousButtons/)
  assert.doesNotMatch(runtime, /window\.confirm/)
})

test('dangerous drawer confirmation uses an accessible custom dialog surface', () => {
  assert.match(runtime, /role', 'dialog'/)
  assert.match(runtime, /aria-modal', 'true'/)
  assert.match(runtime, /aria-labelledby/)
  assert.match(runtime, /aria-describedby/)
  assert.match(runtime, /确认释放回队列/)
  assert.match(runtime, /确认恢复 AI/)
  assert.match(a11yCss, /\.a11y-danger-confirm-overlay/)
  assert.match(a11yCss, /\.a11y-danger-confirm-dialog/)
  assert.match(a11yCss, /\.a11y-danger-confirm-actions/)
})

test('a11y runtime repairs dynamic route content without adding blocking dependencies', () => {
  assert.match(runtime, /MutationObserver/)
  assert.match(runtime, /requestAnimationFrame/)
  assert.match(runtime, /document\.addEventListener\('click', interceptDangerousDrawerActions, true\)/)
  assert.match(runtime, /attributeFilter: \['class', 'role', 'aria-label', 'aria-selected', 'data-active'\]/)
})
