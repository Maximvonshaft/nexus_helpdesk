import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const route = readFileSync(resolve(root, 'src/routes/bulletins.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')

test('bulletin center uses unified routeAccess and command entrypoints', () => {
  assert.match(rbac, /'\/bulletins': \{\}/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/bulletins'\]\}>/)
  assert.match(appShell, /to: '\/bulletins'[\s\S]*label: '公告口径'[\s\S]*access: routeAccess\['\/bulletins'\]/)
  assert.doesNotMatch(appShell, /permission: 'bulletins'/)
  assert.match(commandPalette, /id: 'bulletins'[\s\S]*to: '\/bulletins'[\s\S]*access: routeAccess\['\/bulletins'\]/)
  assert.match(commandPalette, /id: 'new-bulletin'[\s\S]*CAPABILITIES\.bulletinManage/)
})

test('bulletin center exposes real impact preview through shared API client', () => {
  assert.match(types, /export interface BulletinImpactPreviewPayload/)
  assert.match(types, /export interface BulletinImpactPreview/)
  assert.match(apiClient, /previewBulletinImpact: \(payload: BulletinImpactPreviewPayload\) => request<BulletinImpactPreview>\('\/api\/admin\/bulletins\/impact-preview'/)
  assert.match(route, /api\.previewBulletinImpact\(impactPayload\(form\)\)/)
  assert.match(route, /data-testid="bulletin-impact-preview"/)
  assert.match(route, /预览影响工单/)
  assert.match(route, /impactMutation\.data\.matching_tickets/)
  assert.match(route, /impactMutation\.data\.ready_to_reply_tickets/)
  assert.match(route, /impactMutation\.data\.channel_counts/)
  assert.match(route, /impactMutation\.data\.sample_tickets/)
})

test('bulletin create and update payloads keep scope fields', () => {
  assert.match(route, /market_id: form\.market_id \|\| null/)
  assert.match(route, /country_code: form\.country_code \|\| null/)
  assert.match(route, /starts_at: form\.starts_at \|\| null/)
  assert.match(route, /ends_at: form\.ends_at \|\| null/)
  assert.match(apiClient, /updateBulletin: \(bulletinId: number, payload: Partial<Bulletin>\)/)
})
