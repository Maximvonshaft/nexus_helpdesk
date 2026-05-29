import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const route = readFileSync(resolve(root, 'src/routes/webcall-ai-monitor.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const shell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const palette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')

test('webcall ai monitor is a runtime-managed route with shell and command entrypoints', () => {
  assert.match(route, /path: '\/webcall-ai-monitor'/)
  assert.match(route, /RequireCapability requirement=\{routeAccess\['\/webcall-ai-monitor'\]\}/)
  assert.match(router, /WebCallAIMonitorRoute/)
  assert.match(router, /@\/routes\/webcall-ai-monitor/)
  assert.match(shell, /to: '\/webcall-ai-monitor'/)
  assert.match(shell, /access: routeAccess\['\/webcall-ai-monitor'\]/)
  assert.match(palette, /id: 'webcall-ai-monitor'/)
  assert.match(palette, /to: '\/webcall-ai-monitor'/)
  assert.match(rbac, /'\/webcall-ai-monitor': \{ allOf: \[CAPABILITIES\.runtimeManage\] \}/)
})

test('webcall ai monitor uses the unified api client and real admin endpoints', () => {
  assert.match(apiClient, /webcallAIMonitorHealth/)
  assert.match(apiClient, /\/api\/admin\/webcall-ai\/health/)
  assert.match(apiClient, /webcallAIMonitorSessions/)
  assert.match(apiClient, /\/api\/admin\/webcall-ai\/sessions\?/)
  assert.match(apiClient, /webcallAIMonitorEvents/)
  assert.match(apiClient, /\/api\/admin\/webcall-ai\/sessions\/\$\{sessionId\}\/events/)
  assert.match(apiClient, /webcallAIMonitorForceEnd/)
  assert.match(apiClient, /\/api\/admin\/webcall-ai\/sessions\/\$\{sessionId\}\/force-end/)
  assert.doesNotMatch(route, /fetch\(/)
})

test('webcall ai monitor exposes production health sessions events and guarded force end', () => {
  assert.match(types, /interface WebCallAIAdminHealth/)
  assert.match(types, /interface WebCallAIAdminSession/)
  assert.match(types, /interface WebCallAIAdminEvent/)
  assert.match(types, /interface WebCallAIMonitorForceEndResult/)
  assert.match(route, /WebCall AI Monitor/)
  assert.match(route, /webcallAIMonitorHealth/)
  assert.match(route, /webcallAIMonitorSessions/)
  assert.match(route, /webcallAIMonitorEvents/)
  assert.match(route, /webcallAIMonitorForceEnd/)
  assert.match(route, /ConfirmDialog/)
  assert.match(route, /actionAccess\.endWebcallVoice/)
  assert.match(route, /webcall_ai\.session\.ended/)
  assert.match(route, /data-testid="webcall-ai-monitor-workbench"/)
})
