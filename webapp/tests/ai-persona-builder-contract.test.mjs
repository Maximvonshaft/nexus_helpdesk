import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const route = readFileSync(resolve(root, 'src/routes/ai-persona.tsx'), 'utf8')
const api = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')

test('ai persona builder is a top-level registered route with navigation entry', () => {
  assert.match(router, /AIPersonaRoute/)
  assert.match(router, /@\/routes\/ai-persona/)
  assert.match(route, /path: '\/ai-persona'/)
  assert.match(appShell, /label: 'AI 运营'/)
  assert.match(appShell, /to: '\/ai-persona'[\s\S]*AI Persona Builder/)
  assert.match(commandPalette, /打开 AI Persona Builder/)
})

test('ai persona builder is guarded by routeAccess and preserves read/manage split', () => {
  assert.match(rbac, /'\/ai-persona': \{ anyOf: \[CAPABILITIES\.aiConfigRead, CAPABILITIES\.aiConfigManage\] \}/)
  assert.match(route, /RequireCapability/)
  assert.match(route, /routeAccess\['\/ai-persona'\]/)
  assert.match(route, /canAccess\(session\.data, \{ allOf: \[CAPABILITIES\.aiConfigManage\] \}\)/)
  assert.match(route, /只读模拟/)
})

test('ai persona builder uses unified api client including resolve-preview simulation', () => {
  assert.match(types, /export interface PersonaResolvePreview/)
  assert.match(api, /personaResolvePreview:/)
  assert.match(api, /request<PersonaResolvePreview>\('\/api\/persona-profiles\/resolve-preview'/)
  assert.match(route, /api\.personaResolvePreview/)
  assert.match(route, /api\.personaProfiles/)
  assert.match(route, /api\.personaProfile/)
  assert.match(route, /api\.createPersonaProfile/)
  assert.match(route, /api\.updatePersonaProfile/)
  assert.match(route, /api\.publishPersonaProfile/)
  assert.match(route, /api\.rollbackPersonaProfile/)
  assert.doesNotMatch(route, /\bfetch\(/)
})

test('ai persona builder exposes template-required product controls', () => {
  assert.match(route, /Identity statement/)
  assert.match(route, /Identity answer rule/)
  assert.match(route, /Capabilities/)
  assert.match(route, /Disallowed identity claims/)
  assert.match(route, /Handoff boundary/)
  assert.match(route, /Simulation \/ Resolve Preview/)
  assert.match(route, /Release \/ Rollback Evidence/)
  assert.match(route, /GuidedWorkflow/)
})
