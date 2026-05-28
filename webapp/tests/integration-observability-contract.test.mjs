import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const route = readFileSync(resolve(root, 'src/routes/integration-observability.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const api = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')

test('integration observability route is registered and runtime gated', () => {
  assert.match(route, /path: '\/integration-observability'/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/integration-observability'\]\}>/)
  assert.match(router, /IntegrationObservabilityRoute/)
  assert.match(router, /@\/routes\/integration-observability/)
  assert.match(rbac, /'\/integration-observability': \{ allOf: \[CAPABILITIES\.runtimeManage\] \}/)
  assert.match(appShell, /to: '\/integration-observability'[\s\S]*label: 'Integration 观测'[\s\S]*access: routeAccess\['\/integration-observability'\]/)
  assert.match(commandPalette, /id: 'integration-observability'[\s\S]*to: '\/integration-observability'[\s\S]*access: routeAccess\['\/integration-observability'\]/)
})

test('integration observability uses unified API client and real backend contract', () => {
  assert.match(api, /integrationObservability: \(params\?: IntegrationObservabilityQuery\) => request<IntegrationObservabilityResponse>\(`\/api\/admin\/integration-observability/)
  assert.match(api, /exportIntegrationObservabilityCsv: \(params\?: IntegrationObservabilityQuery\) => requestText\(`\/api\/admin\/integration-observability\/export\.csv/)
  assert.match(types, /export interface IntegrationObservabilityResponse/)
  assert.match(types, /export interface IntegrationRequestLogItem/)
  assert.match(route, /api\.integrationObservability\(params\)/)
  assert.match(route, /api\.exportIntegrationObservabilityCsv/)
  assert.match(route, /request_hash 只显示是否存在/)
  assert.doesNotMatch(route, /\bfetch\s*\(/)
  assert.doesNotMatch(route, /secret_hash/)
})

test('integration observability explicitly marks backend gaps instead of faking writes', () => {
  assert.match(route, /client write API/)
  assert.match(route, /not implemented/)
  assert.match(route, /latency .*not persisted/)
  assert.match(route, /不提供假新增 client 入口/)
})
