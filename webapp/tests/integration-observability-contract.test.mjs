import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const route = readFileSync(resolve(root, 'src/routes/integration.tsx'), 'utf8')
const api = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')

test('integration observability uses the real admin API client contract', () => {
  assert.match(types, /export interface IntegrationObservabilityItem/)
  assert.match(types, /request_id\?: string \| null/)
  assert.match(types, /retryable: boolean/)
  assert.match(types, /status_bucket: IntegrationStatusBucket/)
  assert.match(api, /integrationObservabilityRequests/)
  assert.match(api, /\/api\/admin\/integration-observability\/requests/)
})

test('integration observability route is not a mock-only template page', () => {
  assert.match(route, /path: '\/integration'/)
  assert.match(route, /RequireCapability requirement=\{routeAccess\['\/integration'\]\}/)
  assert.match(route, /api\.integrationObservabilityRequests/)
  assert.match(route, /Integration Request Log/)
  assert.match(route, /request_id/)
  assert.match(route, /idempotency/)
  assert.match(route, /retryability/i)
  assert.match(route, /response_preview/)
  assert.doesNotMatch(route, /mockServiceWorker|fixtures|TODO/)
})

test('integration observability is present in nav, command palette and runtime RBAC', () => {
  assert.match(rbac, /'\/integration': \{ allOf: \[CAPABILITIES\.runtimeManage\] \}/)
  assert.match(appShell, /to: '\/integration'[\s\S]*access: routeAccess\['\/integration'\]/)
  assert.match(commandPalette, /id: 'integration-observability'/)
  assert.match(commandPalette, /to: '\/integration'[\s\S]*access: routeAccess\['\/integration'\]/)
})
