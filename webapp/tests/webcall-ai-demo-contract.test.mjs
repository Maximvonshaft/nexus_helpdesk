import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const route = readFileSync(resolve(root, 'src/routes/webcall-ai-demo.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const shell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')

test('webcall ai demo uses admin api client and internal route only', () => {
  assert.match(apiClient, /webcallAIDemoStatus/)
  assert.match(apiClient, /\/api\/admin\/webcall-ai-demo\/status/)
  assert.match(apiClient, /webcallAIDemoCreateSession/)
  assert.match(apiClient, /webcallAIDemoTurn/)
  assert.match(apiClient, /webcallAIDemoEndSession/)
  assert.match(route, /WebCall AI Demo Sandbox/)
  assert.match(route, /browser_speech_supported/)
  assert.match(route, /speechSynthesis/)
  assert.match(route, /typed fallback/)
  assert.doesNotMatch(route, /\/api\/webchat\/voice\/runtime-config/)
  assert.doesNotMatch(route, /fetch\(/)
})

test('webcall ai demo route is visible only through ops navigation', () => {
  assert.match(router, /WebCallAIDemoRoute/)
  assert.match(router, /@\/routes\/webcall-ai-demo/)
  assert.match(shell, /\/webcall-ai-demo/)
  assert.match(shell, /access: routeAccess\['\/webcall-ai-demo'\]/)
  assert.match(rbac, /'\/webcall-ai-demo': \{ allOf: \[CAPABILITIES\.runtimeManage\] \}/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/webcall-ai-demo'\]\}>/)
  assert.match(route, /canAccess\(session\.data, routeAccess\['\/webcall-ai-demo'\]\)/)
})
