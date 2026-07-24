import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const shell = read('src/app/AppShell.tsx')
const navigation = read('src/app/AppNavigation.tsx')

test('the canonical shell provides one responsive navigation surface', () => {
  assert.match(shell, /useMediaQuery\(theme\.breakpoints\.up\('lg'\)/)
  assert.match(shell, /<Drawer/)
  assert.match(shell, /id="nd-mobile-navigation"/)
  assert.match(shell, /aria-label="打开主导航"/)
  assert.match(shell, /orientation="vertical"/)
  assert.doesNotMatch(shell, /workspace-v2|new-workspace|ui-v2/)
})

test('work scope and operator controls remain reachable below desktop width', () => {
  assert.match(shell, /function WorkScopeControl/)
  assert.match(shell, /compact \/>/)
  assert.match(shell, /<AgentPresenceControl capabilities=\{capabilities\} \/>/)
  assert.match(shell, /<IncomingVoiceCallControl capabilities=\{capabilities\} \/>/)
  assert.match(shell, /账户设置/)
  assert.match(shell, /退出登录/)
})

test('navigation exposes vertical and horizontal presentations from one authority', () => {
  assert.match(navigation, /orientation\?: 'horizontal' \| 'vertical'/)
  assert.match(navigation, /const vertical = orientation === 'vertical'/)
  assert.match(navigation, /aria-current=\{active \? 'page' : undefined\}/)
  assert.match(navigation, /borderInlineStart: vertical \? 3 : 0/)
  assert.doesNotMatch(navigation, /scrollbarWidth: 'none'/)
})
