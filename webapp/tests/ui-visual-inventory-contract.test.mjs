import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const REPO_ROOT = resolve(WEBAPP_ROOT, '..')
const INVENTORY_PATH = join(WEBAPP_ROOT, 'design', 'ui-visual-inventory.v1.json')

function inventory() {
  assert.equal(existsSync(INVENTORY_PATH), true, 'visual inventory is missing')
  return JSON.parse(readFileSync(INVENTORY_PATH, 'utf8'))
}

test('visual inventory is versioned and records the selected MUI decision', () => {
  const value = inventory()
  assert.equal(value.schema, 'nexus.ui-visual-inventory.v1')
  assert.equal(value.version, 'ui_visual_inventory.v2')
  assert.equal(value.work_item, 753)
  assert.equal(value.baseline.branch, 'main')
  assert.match(value.baseline.commit, /^[0-9a-f]{40}$/)
  assert.equal(value.decision.framework, 'Material UI')
  assert.equal(value.decision.package, '@mui/material')
  assert.equal(value.decision.version, '9.2.0')
  assert.equal(value.decision.status, 'code_migration_complete_verification_pending')
})

test('active visual authorities are bounded to the one MUI theme and minimal global CSS', () => {
  const value = inventory()
  const activePaths = value.active_visual_authorities.map((item) => item.path).sort()
  assert.deepEqual(activePaths, [
    'webapp/src/a11y.css',
    'webapp/src/styles.css',
    'webapp/src/theme/NexusThemeProvider.tsx',
    'webapp/src/theme/nexusTheme.ts',
  ])
  for (const item of value.active_visual_authorities) {
    assert.equal(existsSync(join(REPO_ROOT, item.path)), true, `active visual authority is missing: ${item.path}`)
    assert.ok(item.role?.trim(), `active visual authority has no role: ${item.path}`)
  }
})

test('every retired visual path is physically absent', () => {
  const value = inventory()
  assert.equal(new Set(value.retired_visual_paths).size, value.retired_visual_paths.length)
  for (const path of value.retired_visual_paths) {
    assert.equal(existsSync(join(REPO_ROOT, path)), false, `retired visual path returned: ${path}`)
  }
})

test('all original systemic findings have explicit root-level resolutions', () => {
  const value = inventory()
  const ids = value.resolved_findings.map((item) => item.id).sort()
  assert.deepEqual(ids, ['VIS-001', 'VIS-002', 'VIS-003', 'VIS-004', 'VIS-005', 'VIS-006', 'VIS-007', 'VIS-008'])
  for (const item of value.resolved_findings) {
    assert.ok(item.finding?.trim())
    assert.ok(item.resolution?.trim())
  }
})

test('all active routes migrated without creating a second product spine', () => {
  const value = inventory()
  for (const route of ['/login', '/workspace', '/knowledge', '/channels', '/runtime', '/control-tower']) {
    const result = value.route_results.find((item) => item.route === route)
    assert.equal(result?.mui_migrated, true, `route has not migrated to MUI: ${route}`)
    assert.ok(result?.functional_behavior_preserved?.trim())
  }
  const compatibility = value.route_results.find((item) => item.route === '/webchat')
  assert.equal(compatibility?.mui_migrated, false)
  assert.match(compatibility?.functional_behavior_preserved ?? '', /compatibility redirect only/)
})

test('non-duplication result and remaining verification are explicit', () => {
  const value = inventory()
  for (const [key, result] of Object.entries(value.non_duplication_result)) {
    assert.equal(result, false, `duplicate implementation remains: ${key}`)
  }
  assert.ok(value.remaining_acceptance.some((item) => item.includes('package-lock.json')))
  assert.ok(value.remaining_acceptance.some((item) => item.includes('architecture, lint, typecheck')))
  assert.ok(value.remaining_acceptance.some((item) => item.includes('independent exact-head review')))
})
