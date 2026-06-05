import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const runtime = read('src/a11yRuntime.ts')
const main = read('src/main.tsx')

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

test('a11y runtime repairs WebCall queue filters without pretending they are tabs', () => {
  assert.match(runtime, /repairWebcallQueueFilters/)
  assert.match(runtime, /\[role="tablist"\]\[aria-label="WebCall Operational Queue tabs"\]/)
  assert.match(runtime, /setAttr\(group, 'role', 'group'\)/)
  assert.match(runtime, /WebCall Operational Queue filters/)
  assert.match(runtime, /aria-pressed/)
})

test('a11y runtime repairs dynamic route content without adding blocking dependencies', () => {
  assert.match(runtime, /MutationObserver/)
  assert.match(runtime, /requestAnimationFrame/)
  assert.match(runtime, /attributeFilter: \['class', 'role', 'aria-label', 'aria-selected', 'data-active'\]/)
  assert.doesNotMatch(runtime, /window\.confirm/)
})
