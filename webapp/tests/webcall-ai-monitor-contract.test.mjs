import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const route = read('src/routes/webcall-ai-monitor.tsx')
const apiClient = read('src/lib/api.ts')
const rbac = read('src/lib/rbac.ts')
const router = read('src/router.tsx')
const shell = read('src/layouts/AppShell.tsx')
const commandPalette = read('src/components/ui/CommandPalette.tsx')
const backendApi = read('../backend/app/api/admin_webcall_ai.py')
const backendSchemas = read('../backend/app/schemas.py')
const backendPermissions = read('../backend/app/services/permissions.py')

test('WebCall AI Monitor is a top-level admin route exposed through guarded nav and command palette', () => {
  assert.match(route, /path: '\/webcall-ai-monitor'/)
  assert.match(route, /data-testid="webcall-ai-monitor-workbench"/)
  assert.match(router, /WebCallAIMonitorRoute/)
  assert.match(router, /@\/routes\/webcall-ai-monitor/)
  assert.match(shell, /to: '\/webcall-ai-monitor'[\s\S]*access: routeAccess\['\/webcall-ai-monitor'\]/)
  assert.match(commandPalette, /id: 'webcall-ai-monitor'/)
  assert.match(commandPalette, /to: '\/webcall-ai-monitor'[\s\S]*access: routeAccess\['\/webcall-ai-monitor'\]/)
})

test('WebCall AI Monitor uses shared API client methods and typed backend response models', () => {
  assert.match(apiClient, /export type WebCallAIHealth/)
  assert.match(apiClient, /webcallAIHealth: \(\) => request<WebCallAIHealth>\('\/api\/admin\/webcall-ai\/health'\)/)
  assert.match(apiClient, /webcallAISessions: \(params\?: \{ status\?: string; limit\?: number \}\)/)
  assert.match(apiClient, /webcallAISessionEvents/)
  assert.match(apiClient, /webcallAIForceEndSession/)
  assert.match(route, /api\.webcallAIHealth/)
  assert.match(route, /api\.webcallAISessions/)
  assert.match(route, /api\.webcallAISessionEvents/)
  assert.match(backendApi, /response_model=WebCallAIHealthRead/)
  assert.match(backendApi, /response_model=WebCallAISessionListRead/)
  assert.match(backendApi, /response_model=WebCallAIEventsRead/)
  assert.match(backendSchemas, /class WebCallAIHealthRead\(APIModel\):/)
  assert.match(backendSchemas, /class WebCallAISessionRead\(APIModel\):/)
})

test('WebCall AI Monitor separates read monitoring from force-end mutation permissions', () => {
  assert.match(rbac, /runtimeRead: 'runtime\.read'/)
  assert.match(rbac, /'\/webcall-ai-monitor': \{ allOf: \[CAPABILITIES\.runtimeRead\] \}/)
  assert.match(rbac, /forceEndWebcallAI: \{ allOf: \[CAPABILITIES\.runtimeManage, CAPABILITIES\.webcallVoiceEnd\] \}/)
  assert.match(backendPermissions, /CAP_RUNTIME_READ = "runtime\.read"/)
  assert.match(backendPermissions, /def ensure_can_read_runtime/)
  assert.match(backendApi, /ensure_can_read_runtime\(current_user, db\)/)
  assert.match(backendApi, /def force_end_admin_webcall_ai_session[\s\S]*ensure_can_manage_runtime\(current_user, db\)/)
})

test('WebCall AI Monitor surfaces production facts from the real WebCall AI backend', () => {
  assert.match(route, /active_sessions/)
  assert.match(route, /stale_leases/)
  assert.match(route, /failed_sessions/)
  assert.match(route, /readiness\.blockers/)
  assert.match(route, /readiness\.degraded/)
  assert.match(route, /webcallAISessionEvents/)
  assert.match(backendApi, /worker_health\(\)/)
  assert.match(backendApi, /WebchatVoiceSession\.mode == "livekit_ai_agent"/)
  assert.match(backendApi, /list_events\(db, session_public_id, require_visitor_token=False\)/)
})
