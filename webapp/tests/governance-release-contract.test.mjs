import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const route = read('src/routes/governance-releases.tsx')
const api = read('src/lib/api.ts')
const rbac = read('src/lib/rbac.ts')
const types = read('src/lib/types.ts')
const appShell = read('src/layouts/AppShell.tsx')
const commandPalette = read('src/components/ui/CommandPalette.tsx')

test('governance release workbench uses unified API client for full lifecycle', () => {
  for (const method of [
    'governanceReleases',
    'createGovernanceRelease',
    'submitGovernanceRelease',
    'approveGovernanceRelease',
    'publishGovernanceRelease',
    'rollbackGovernanceRelease',
    'rejectGovernanceRelease',
  ]) {
    assert.match(api, new RegExp(`${method}:`))
    assert.match(route, new RegExp(`api\\.${method}`))
  }
  assert.match(api, /\/api\/admin\/governance-releases/)
})

test('governance release workbench exposes template approval release rollback shape', () => {
  assert.match(route, /Control Tower \/ Governance/)
  assert.match(route, /审批、发布、回滚证据链/)
  assert.match(route, /Persona、Knowledge、公告口径、发送线路和 Speedaf/)
  assert.match(route, /sourceOptions = \['ai_config', 'persona', 'knowledge', 'bulletin', 'channel_account', 'outbound_email', 'speedaf_action'\]/)
  assert.match(route, /allowedActions/)
  assert.match(route, /impact_json/)
  assert.match(route, /diff_json/)
  assert.match(route, /rollback_plan/)
  assert.match(route, /request_id/)
})

test('governance release RBAC, nav, command palette and types are wired', () => {
  assert.match(rbac, /governanceReleaseRead: 'governance\.release\.read'/)
  assert.match(rbac, /governanceReleaseManage: 'governance\.release\.manage'/)
  assert.match(rbac, /manageGovernanceRelease/)
  assert.match(types, /export interface GovernanceRelease/)
  assert.match(types, /export interface GovernanceReleaseEvent/)
  assert.match(types, /export interface GovernanceReleaseList/)
  assert.match(appShell, /to: '\/governance-releases'/)
  assert.match(commandPalette, /governance-releases/)
})
