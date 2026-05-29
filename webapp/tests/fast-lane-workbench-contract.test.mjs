import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const route = read('src/routes/fast-lane.tsx')
const api = read('src/lib/api.ts')
const types = read('src/lib/types.ts')
const rbac = read('src/lib/rbac.ts')
const router = read('src/router.tsx')
const appShell = read('src/layouts/AppShell.tsx')
const commandPalette = read('src/components/ui/CommandPalette.tsx')
const backendStats = read('../backend/app/api/stats.py')
const backendPermissions = read('../backend/app/services/permissions.py')
const backendSchemas = read('../backend/app/schemas.py')
const backendTests = read('../backend/tests/test_webchat_fast_routing_stats_policy.py')

test('Fast Lane workbench is registered as a top-level route and operator entry', () => {
  assert.match(router, /FastLaneRoute/)
  assert.match(router, /@\/routes\/fast-lane/)
  assert.match(route, /path: '\/fast-lane'/)
  assert.match(appShell, /to: '\/fast-lane'[\s\S]*Fast Lane 看板/)
  assert.match(appShell, /label: '运营管理'[\s\S]*'\/fast-lane'/)
  assert.match(commandPalette, /id: 'fast-lane-workbench'/)
  assert.match(commandPalette, /to: '\/fast-lane'[\s\S]*routeAccess\['\/fast-lane'\]/)
})

test('Fast Lane frontend reads the real stats API through the shared client', () => {
  assert.match(types, /export interface WebchatFastStats/)
  assert.match(types, /ticketless_sessions: number/)
  assert.match(types, /errors_by_code: Record<string, number>/)
  assert.match(api, /WebchatFastStats/)
  assert.match(api, /webchatFastStats: \(days = 7\) => request<WebchatFastStats>\(`\/api\/stats\/webchat-fast\?days=/)
  assert.match(route, /api\.webchatFastStats\(days\)/)
  assert.match(route, /data-testid="fast-lane-workbench"/)
  assert.match(route, /Ticketless sessions/)
  assert.match(route, /AI resolved rate/)
  assert.match(route, /Handoff rate/)
  assert.doesNotMatch(route, /fastLaneStats\.map/)
})

test('Fast Lane access is backed by stats.read on frontend and backend', () => {
  assert.match(rbac, /statsRead: 'stats\.read'/)
  assert.match(rbac, /'\/fast-lane': \{ allOf: \[CAPABILITIES\.statsRead\] \}/)
  assert.match(backendPermissions, /CAP_STATS_READ = "stats\.read"/)
  assert.match(backendPermissions, /def ensure_can_read_stats/)
  assert.match(backendPermissions, /stats_read_requires_capability/)
  assert.match(backendStats, /ensure_can_read_stats\(current_user, db\)/)
})

test('Backend stats endpoint has a typed contract and RBAC regression coverage', () => {
  assert.match(backendSchemas, /class WebchatFastStatsRead\(BaseModel\):/)
  assert.match(backendSchemas, /handoff_rate: float/)
  assert.match(backendStats, /response_model=WebchatFastStatsRead/)
  assert.match(backendTests, /test_webchat_fast_stats_endpoint_requires_stats_read_capability/)
  assert.match(backendTests, /stats_read_requires_capability/)
})
