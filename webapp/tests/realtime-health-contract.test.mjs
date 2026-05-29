import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const route = read('src/routes/realtime.tsx')
const apiClient = read('src/lib/api.ts')
const types = read('src/lib/types.ts')
const rbac = read('src/lib/rbac.ts')
const appShell = read('src/layouts/AppShell.tsx')
const commandPalette = read('src/components/ui/CommandPalette.tsx')
const backendEvents = read('../backend/app/api/webchat_events.py')
const backendSchemas = read('../backend/app/schemas.py')
const backendPermissions = read('../backend/app/services/permissions.py')
const backendTests = read('../backend/tests/test_webchat_realtime_health_api.py')

test('Realtime Health is a top-level route, navigation entry, and command palette action', () => {
  assert.match(route, /path: '\/realtime'/)
  assert.match(route, /data-testid="realtime-health-workbench"/)
  assert.match(appShell, /to: '\/realtime'[\s\S]*access: routeAccess\['\/realtime'\]/)
  assert.match(commandPalette, /id: 'realtime-health'/)
  assert.match(commandPalette, /to: '\/realtime'[\s\S]*access: routeAccess\['\/realtime'\]/)
})

test('Realtime Health uses the shared API client and typed backend contract', () => {
  assert.match(apiClient, /webchatRealtimeHealth: \(\) => request<WebchatRealtimeHealth>\('\/api\/webchat\/admin\/realtime-health'\)/)
  assert.match(types, /export interface WebchatRealtimeHealth/)
  assert.match(route, /queryKey: \['webchatRealtimeHealth'\]/)
  assert.match(route, /api\.webchatRealtimeHealth/)
  assert.match(backendEvents, /@router\.get\("\/admin\/realtime-health", response_model=WebchatRealtimeHealthRead\)/)
  assert.match(backendSchemas, /class WebchatRealtimeHealthRead\(APIModel\):/)
})

test('Realtime Health exposes real runtime facts instead of static template text', () => {
  assert.match(backendEvents, /webchat_realtime_hub\.snapshot/)
  assert.match(backendEvents, /webchat_realtime_broker_status/)
  assert.match(backendEvents, /func\.max\(WebchatEvent\.id\)/)
  assert.match(backendEvents, /WEBCHAT_WS_PUBLIC_ENABLED|webchat_ws_public_enabled/)
  assert.match(route, /VITE_WEBCHAT_WS_ENABLED/)
  assert.match(route, /after_id polling/)
  assert.match(route, /connection\.hello/)
})

test('Realtime Health is protected by a read-only realtime capability on both sides', () => {
  assert.match(rbac, /webchatRealtimeMonitor: 'webchat\.realtime\.monitor'/)
  assert.match(rbac, /'\/realtime': \{ allOf: \[CAPABILITIES\.webchatRealtimeMonitor\] \}/)
  assert.match(backendPermissions, /CAP_WEBCHAT_REALTIME_MONITOR = "webchat\.realtime\.monitor"/)
  assert.match(backendPermissions, /ensure_can_monitor_webchat_realtime/)
  assert.match(backendTests, /webchat_realtime_monitor_requires_capability/)
})
