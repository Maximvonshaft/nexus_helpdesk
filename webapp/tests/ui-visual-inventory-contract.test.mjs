import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const root = resolve(process.cwd())
const repo = resolve(root, '..')
const value = JSON.parse(readFileSync(join(root, 'design', 'ui-visual-inventory.v1.json'), 'utf8'))

test('visual inventory records the final source-converged MUI decision', () => {
  assert.equal(value.schema, 'nexus.ui-visual-inventory.v1')
  assert.equal(value.version, 'ui_visual_inventory.v3')
  assert.equal(value.work_item, 753)
  assert.equal(value.decision.framework, 'Material UI')
  assert.equal(value.decision.version, '9.2.0')
  assert.equal(value.decision.status, 'source_convergence_complete_verification_pending')
})

test('active visual authorities are bounded and exist', () => {
  assert.deepEqual(value.active_visual_authorities.map((item) => item.path).sort(), [
    'webapp/src/a11y.css',
    'webapp/src/app/OperatorPresentation.tsx',
    'webapp/src/styles.css',
    'webapp/src/theme/NexusThemeProvider.tsx',
    'webapp/src/theme/nexusTheme.ts',
  ])
  for (const item of value.active_visual_authorities) {
    assert.equal(existsSync(join(repo, item.path)), true, item.path)
    assert.ok(item.role?.trim())
  }
})

test('every retired path is absent and every root finding has a resolution', () => {
  assert.equal(new Set(value.retired_visual_paths).size, value.retired_visual_paths.length)
  for (const path of value.retired_visual_paths) assert.equal(existsSync(join(repo, path)), false, path)
  assert.deepEqual(value.resolved_findings.map((item) => item.id).sort(), Array.from({ length: 12 }, (_, index) => `VIS-${String(index + 1).padStart(3, '0')}`))
  for (const item of value.resolved_findings) {
    assert.ok(item.finding?.trim())
    assert.ok(item.resolution?.trim())
  }
})

test('Knowledge and Workspace implementation graphs have no second authority', () => {
  const knowledge = value.implementation_graph.knowledge
  assert.equal(knowledge.mode, 'canManage')
  assert.equal(knowledge.second_page, false)
  assert.equal(existsSync(join(repo, knowledge.page)), true)
  const workspace = value.implementation_graph.workspace
  assert.equal(workspace.second_store, false)
  assert.equal(workspace.second_renderer, false)
  assert.equal(workspace.second_api, false)
  for (const path of Object.values(workspace).filter((item) => typeof item === 'string' && item.startsWith('webapp/'))) assert.equal(existsSync(join(repo, path)), true, path)
})

test('routes are migrated, duplication is false and acceptance remains explicit', () => {
  for (const route of ['/login', '/workspace', '/knowledge', '/channels', '/runtime', '/control-tower']) assert.equal(value.route_results.find((item) => item.route === route)?.mui_migrated, true)
  assert.equal(value.route_results.find((item) => item.route === '/webchat')?.mui_migrated, false)
  for (const result of Object.values(value.non_duplication_result)) assert.equal(result, false)
  assert.ok(value.remaining_acceptance.some((item) => item.includes('package-lock.json')))
  assert.ok(value.remaining_acceptance.some((item) => item.includes('architecture, lint, typecheck')))
  assert.ok(value.remaining_acceptance.some((item) => item.includes('independent exact-head review')))
})
