import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const shell = read('src/app/AppShell.tsx')
const navigation = read('src/app/AppNavigation.tsx')
const presence = read('src/app/AgentPresenceControl.tsx')
const incomingVoice = read('src/app/IncomingVoiceCallControl.tsx')

test('the canonical shell provides one responsive navigation surface', () => {
  assert.match(shell, /useMediaQuery\(theme\.breakpoints\.up\('lg'\)/)
  assert.match(shell, /<Drawer/)
  assert.match(shell, /id="nd-mobile-navigation"/)
  assert.match(shell, /aria-label="打开主导航"/)
  assert.match(shell, /function CanonicalAppNavigation/)
  assert.match(shell, /vertical\n\s+onNavigate=/)
  assert.equal((shell.match(/<AppNavigation/g) ?? []).length, 1)
  assert.doesNotMatch(shell, /workspace-v2|new-workspace|ui-v2/)
})

test('live operator runtimes remain mounted above the temporary Drawer', () => {
  assert.doesNotMatch(shell, /keepMounted/)
  assert.match(shell, /<AgentPresenceProvider capabilities=\{capabilities\}>/)
  assert.match(shell, /<IncomingVoiceCallProvider capabilities=\{capabilities\}>/)
  assert.equal((shell.match(/<AgentPresenceProvider/g) ?? []).length, 1)
  assert.equal((shell.match(/<IncomingVoiceCallProvider/g) ?? []).length, 1)
  assert.match(presence, /export function AgentPresenceProvider/)
  assert.match(presence, /refetchInterval: 30_000/)
  assert.match(presence, /agentRoutingApi\.heartbeat\(\)/)
  assert.match(incomingVoice, /export function IncomingVoiceCallProvider/)
  assert.match(incomingVoice, /refetchInterval: 2_000/)
  assert.match(incomingVoice, /<Dialog/)
})

test('work scope and operator controls remain interactive below desktop width', () => {
  assert.match(shell, /function WorkScopeControl/)
  assert.match(shell, /compact \/>/)
  assert.match(shell, /<AgentPresenceControl presentation="drawer" \/>/)
  assert.match(shell, /<IncomingVoiceCallControl \/>/)
  assert.match(presence, /presentation === 'drawer'/)
  assert.match(presence, /width: drawer \? '100%' : 'auto'/)
  assert.doesNotMatch(presence, /display: \{ xs: 'none', lg: 'flex' \}/)
  assert.match(shell, /function AccountNavigationLink/)
  assert.equal((shell.match(/to="\/account"/g) ?? []).length, 1)
  assert.match(shell, /账户设置/)
  assert.match(shell, /<IconButton aria-label="退出"/)
  assert.match(shell, /退出登录/)
})

test('navigation exposes vertical and horizontal presentations from one authority', () => {
  assert.match(navigation, /orientation\?: 'horizontal' \| 'vertical'/)
  assert.match(navigation, /const vertical = orientation === 'vertical'/)
  assert.match(navigation, /aria-current=\{active \? 'page' : undefined\}/)
  assert.match(navigation, /borderInlineStart: vertical \? 3 : 0/)
  assert.doesNotMatch(navigation, /scrollbarWidth: 'none'/)
})
