import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const route = readFileSync(resolve(root, 'src/routes/speedaf.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const speedafApi = readFileSync(resolve(root, 'src/lib/speedafApi.ts'), 'utf8')
const speedafPanel = readFileSync(resolve(root, 'src/components/operator/SpeedafActionsPanel.tsx'), 'utf8')

test('speedaf action center is a first-class guarded route', () => {
  assert.match(router, /SpeedafRoute/)
  assert.match(router, /@\/routes\/speedaf/)
  assert.match(route, /path: '\/speedaf'/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/speedaf'\]\}>/)
  assert.match(rbac, /'\/speedaf': \{ allOf: \[CAPABILITIES\.ticketRead\], anyOf: \[CAPABILITIES\.speedafWorkOrderWrite, CAPABILITIES\.speedafAddressUpdateWrite, CAPABILITIES\.speedafCancelWrite\] \}/)
})

test('speedaf action center is reachable from AppShell and CommandPalette', () => {
  assert.match(appShell, /to: '\/speedaf'/)
  assert.match(appShell, /Speedaf 动作/)
  assert.match(appShell, /access: routeAccess\['\/speedaf'\]/)
  assert.match(commandPalette, /speedaf-action-center/)
  assert.match(commandPalette, /to: '\/speedaf'/)
  assert.match(commandPalette, /access: routeAccess\['\/speedaf'\]/)
})

test('speedaf action center uses ticket context, timeline, and real Speedaf action panel', () => {
  assert.match(route, /api\.cases\(\{ q: query \|\| undefined, status: status \|\| undefined, limit: 80 \}\)/)
  assert.match(route, /api\.caseDetail\(selectedId as number\)/)
  assert.match(route, /api\.ticketTimeline\(selectedId as number, \{ limit: 35 \}\)/)
  assert.match(route, /<SpeedafActionsPanel activeCase=\{activeCase\} onToast=\{setToast\} \/>/)
  assert.match(route, /data-testid="speedaf-action-center"/)
  assert.match(route, /data-testid="speedaf-audit-timeline"/)
  assert.match(route, /request_id/)
  assert.match(route, /dedupe_key/)
  assert.match(route, /job_id/)
})

test('speedaf write endpoints are centralized in the unified api client', () => {
  assert.match(apiClient, /speedafCreateWorkOrder: \(ticketId: number, payload: SpeedafWorkOrderPayload\) => request<SpeedafActionResponse>/)
  assert.match(apiClient, /\/api\/tickets\/\$\{ticketId\}\/speedaf\/work-orders/)
  assert.match(apiClient, /speedafAddressUpdate: \(ticketId: number, payload: SpeedafAddressUpdatePayload\) => request<SpeedafActionResponse>/)
  assert.match(apiClient, /\/api\/tickets\/\$\{ticketId\}\/speedaf\/address-update/)
  assert.match(apiClient, /speedafCancelPreview: \(ticketId: number, payload: SpeedafCancelPreviewPayload\) => request<SpeedafCancelPreviewResponse>/)
  assert.match(apiClient, /\/api\/tickets\/\$\{ticketId\}\/speedaf\/cancel-preview/)
  assert.match(apiClient, /speedafCancel: \(ticketId: number, payload: SpeedafCancelPayload\) => request<SpeedafActionResponse>/)
  assert.match(apiClient, /\/api\/tickets\/\$\{ticketId\}\/speedaf\/cancel/)
  assert.match(speedafApi, /createWorkOrder: api\.speedafCreateWorkOrder/)
  assert.match(speedafApi, /addressUpdate: api\.speedafAddressUpdate/)
  assert.match(speedafApi, /cancelPreview: api\.speedafCancelPreview/)
  assert.match(speedafApi, /cancel: api\.speedafCancel/)
  assert.doesNotMatch(speedafApi, /\bfetch\s*\(/)
  assert.doesNotMatch(speedafApi, /Authorization/)
  assert.doesNotMatch(speedafApi, /buildApiUrl/)
})

test('speedaf action panel preserves backend guard semantics and refreshes evidence', () => {
  assert.match(speedafPanel, /speedafApi\.createWorkOrder/)
  assert.match(speedafPanel, /speedafApi\.addressUpdate/)
  assert.match(speedafPanel, /speedafApi\.cancelPreview/)
  assert.match(speedafPanel, /speedafApi\.cancel/)
  assert.match(speedafPanel, /cancelPreview\?\.confirmToken/)
  assert.match(speedafPanel, /<ConfirmDialog/)
  assert.match(speedafPanel, /invalidateQueries\(\{ queryKey: \['caseDetail', activeCase\.id\] \}\)/)
  assert.match(speedafPanel, /invalidateQueries\(\{ queryKey: \['ticketTimeline', activeCase\.id\] \}\)/)
  assert.match(speedafPanel, /invalidateQueries\(\{ queryKey: \['cases'\] \}\)/)
})
