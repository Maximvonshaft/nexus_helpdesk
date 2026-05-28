import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(process.cwd())
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const route = readFileSync(resolve(root, 'src/routes/realtime.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')

test('realtime health route is registered and routeAccess gated', () => {
  assert.match(router, /RealtimeRoute/)
  assert.match(router, /@\/routes\/realtime/)
  assert.match(route, /path: '\/realtime'/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/realtime'\]\}>/)
  assert.match(rbac, /'\/realtime': \{ allOf: \[CAPABILITIES\.runtimeManage\] \}/)
})

test('realtime health is reachable from AppShell navigation and command palette', () => {
  assert.match(appShell, /to: '\/realtime'[\s\S]*label: 'Realtime Health'[\s\S]*access: routeAccess\['\/realtime'\]/)
  assert.match(appShell, /\{ label: '治理与运维', items: \['\/runtime', '\/realtime', '\/ai-control', '\/control-plane', '\/users', '\/webcall-ai-demo'\] \}/)
  assert.match(commandPalette, /id: 'realtime-health'[\s\S]*label: '查看 Realtime Health'[\s\S]*to: '\/realtime'[\s\S]*access: routeAccess\['\/realtime'\]/)
  assert.match(commandPalette, /queryClient\.invalidateQueries\(\{ queryKey: \['realtimeHealth'\] \}\)/)
})

test('realtime health uses unified api client and exposes template metrics', () => {
  assert.match(types, /export interface RealtimeHealth \{/)
  assert.match(apiClient, /realtimeHealth: \(\) => request<RealtimeHealth>\('\/api\/admin\/realtime-health'\)/)
  for (const label of ['WS Enabled', 'Broker', 'Heartbeat', 'Fallback Poll', 'Last Event ID', 'Auth Failures']) {
    assert.match(route, new RegExp(label))
  }
  for (const field of ['broker_cross_worker_safe', 'fallback_poll_ms', 'heartbeat_ms', 'last_event_id', 'auth_failures_total']) {
    assert.match(route, new RegExp(field))
  }
  assert.doesNotMatch(route, /\bfetch\s*\(/)
})
