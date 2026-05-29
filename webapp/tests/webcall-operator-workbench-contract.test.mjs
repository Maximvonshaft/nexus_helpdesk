import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const route = readFileSync(resolve(root, 'src/routes/webcall-operator.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const publicWebcall = readFileSync(resolve(root, 'src/routes/webcall.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')

test('top-level /webcall operator route is registered without replacing customer room route', () => {
  assert.match(route, /path: '\/webcall'/)
  assert.match(route, /WebCall Operator Workbench/)
  assert.match(router, /WebCallOperatorRoute/)
  assert.match(router, /@\/routes\/webcall-operator/)
  assert.match(router, /WebCallRoute/)
  assert.match(publicWebcall, /path: '\/webcall\/\$voice_session_id'/)
})

test('webcall operator entry is routeAccess gated and visible in operator navigation', () => {
  assert.match(rbac, /'\/webcall': \{[\s\S]*CAPABILITIES\.ticketRead[\s\S]*CAPABILITIES\.customerProfileRead[\s\S]*CAPABILITIES\.webcallVoiceQueueView[\s\S]*CAPABILITIES\.webcallVoiceRead[\s\S]*CAPABILITIES\.webcallVoiceAccept[\s\S]*CAPABILITIES\.webchatHandoffAccept[\s\S]*CAPABILITIES\.webchatConversationMonitorAi[\s\S]*CAPABILITIES\.webchatHandoffForceTakeover[\s\S]*\}/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/webcall'\]\}>/)
  assert.match(appShell, /to: '\/webcall'[\s\S]*label: 'WebCall 工作台'[\s\S]*access: routeAccess\['\/webcall'\]/)
  assert.match(appShell, /isActiveNavPath\(location\.pathname, item\.to\)/)
  assert.match(appShell, /pathname\.startsWith\(`\$\{target\}\/`\)/)
  assert.match(commandPalette, /id: 'webcall-workbench'[\s\S]*to: '\/webcall'[\s\S]*access: routeAccess\['\/webcall'\]/)
})

test('webcall workbench uses real backend contracts for queue, identity, AI, handoff, and audit', () => {
  assert.match(apiClient, /webcallOperatorWorkbench/)
  assert.match(apiClient, /\/api\/webcall\/operator\/workbench/)
  assert.match(types, /export interface WebCallOperatorWorkbenchResponse/)
  assert.match(types, /export interface WebCallIdentityVerification/)
  assert.match(route, /api\.webcallOperatorWorkbench/)
  for (const apiCall of [
    'api.webchatAcceptHandoff',
    'api.webchatDeclineHandoff',
    'api.webchatReleaseHandoff',
    'api.webchatResumeAi',
    'api.webchatForceTakeover',
  ]) {
    assert.match(route, new RegExp(apiCall.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.match(route, /<AgentWebCallPanel/)
  assert.match(route, /data-testid="webcall-identity-verification"/)
  assert.match(route, /data-testid="webcall-ai-suggestions"/)
  assert.match(route, /data-testid="webcall-handoff-actions"/)
  assert.match(route, /data-testid="webcall-demo-shape"/)
  assert.match(route, /data-testid="webcall-timeline-audit"/)
  assert.doesNotMatch(route, /\bfetch\s*\(/)
  assert.doesNotMatch(route, /Mock voice session|Accept mock call|End mock call/)
})
