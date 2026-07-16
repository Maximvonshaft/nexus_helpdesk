import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const REPO_ROOT = resolve(WEBAPP_ROOT, '..')
const SRC_ROOT = join(WEBAPP_ROOT, 'src')
const INVENTORY_PATH = join(WEBAPP_ROOT, 'design', 'ui-visual-inventory.v1.json')

const ALLOWED_DISPOSITIONS = new Set([
  'KEEP_AUTHORITY',
  'REWRITE_IN_PLACE',
  'FOLD_AND_DELETE',
  'MIGRATE_AND_DELETE',
  'DELETE_UNUSED',
])

function walk(directory) {
  const files = []
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = join(directory, entry.name)
    if (entry.isDirectory()) files.push(...walk(absolute))
    else files.push(absolute)
  }
  return files
}

function repositoryPath(absolute) {
  return relative(REPO_ROOT, absolute).split('\\').join('/')
}

function inventory() {
  assert.equal(existsSync(INVENTORY_PATH), true, 'visual inventory is missing')
  return JSON.parse(readFileSync(INVENTORY_PATH, 'utf8'))
}

test('visual inventory is versioned and bound to the canonical refinement work item', () => {
  const value = inventory()
  assert.equal(value.schema, 'nexus.ui-visual-inventory.v1')
  assert.equal(value.version, 'ui_visual_inventory.v1')
  assert.equal(value.work_item, 753)
  assert.equal(value.baseline.branch, 'main')
  assert.match(value.baseline.commit, /^[0-9a-f]{40}$/)
  assert.equal(value.external_framework_decision.status, 'DEFERRED_UNTIL_INVENTORY_ACCEPTED')
})

test('every active source stylesheet has exactly one governed disposition', () => {
  const value = inventory()
  const sourceStyles = walk(SRC_ROOT)
    .filter((file) => file.endsWith('.css'))
    .map(repositoryPath)
    .sort()
  const inventoryStyles = value.style_files.map((item) => item.path).sort()

  assert.equal(new Set(inventoryStyles).size, inventoryStyles.length, 'visual inventory contains duplicate style paths')
  assert.deepEqual(inventoryStyles, sourceStyles, 'visual inventory must cover every active source stylesheet and no deleted stylesheet')

  for (const item of value.style_files) {
    assert.equal(ALLOWED_DISPOSITIONS.has(item.disposition), true, `invalid disposition for ${item.path}: ${item.disposition}`)
    assert.equal(existsSync(join(REPO_ROOT, item.path)), true, `inventoried stylesheet does not exist: ${item.path}`)
    assert.ok(item.reason?.trim(), `missing reason for ${item.path}`)
    assert.ok(item.target?.trim(), `missing target for ${item.path}`)
  }
})

test('shared component inventory points only to the canonical component authority', () => {
  const value = inventory()
  for (const item of value.shared_components) {
    assert.match(item.path, /^webapp\/src\/components\/ui\//)
    assert.equal(existsSync(join(REPO_ROOT, item.path)), true, `inventoried shared component does not exist: ${item.path}`)
    assert.equal(ALLOWED_DISPOSITIONS.has(item.disposition), true, `invalid component disposition: ${item.path}`)
  }
})

test('inventory requires replacement cleanup rather than permanent visual layering', () => {
  const value = inventory()
  const refinementLayer = value.style_files.find((item) => item.path.endsWith('operator-workspace-refinements.css'))
  assert.equal(refinementLayer?.disposition, 'FOLD_AND_DELETE')

  assert.ok(value.acceptance.includes('FOLD_AND_DELETE and MIGRATE_AND_DELETE items are physically removed before acceptance.'))
  assert.ok(value.acceptance.includes('No old/new UI switch or parallel framework remains.'))
})

test('missing visual responsibilities are semantic and bounded', () => {
  const value = inventory()
  const names = value.missing_shared_responsibilities.map((item) => item.name)
  assert.deepEqual(names.sort(), ['Count', 'LoadingState', 'Notice', 'StatusIndicator'].sort())
  for (const item of value.missing_shared_responsibilities) {
    assert.ok(item.reason?.trim())
    assert.ok(item.rule?.trim())
  }
})
