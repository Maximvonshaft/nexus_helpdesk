import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const REPO_ROOT = resolve(WEBAPP_ROOT, '..')
const CONTRACT_PATH = join(WEBAPP_ROOT, 'design', 'operator-console-consolidation.v1.json')
const PLAN_PATH = join(REPO_ROOT, 'docs', 'superpowers', 'plans', '2026-07-14-canonical-operator-console-consolidation.md')
const ROUTES_DIR = join(WEBAPP_ROOT, 'src', 'routes')

function read(path) {
  assert.equal(existsSync(path), true, `required path is missing: ${path}`)
  return readFileSync(path, 'utf8')
}

function contract() {
  return JSON.parse(read(CONTRACT_PATH))
}

function byId(items) {
  return new Map(items.map((item) => [item.id, item]))
}

function byRoute(items) {
  return new Map(items.map((item) => [item.route, item]))
}

test('consolidation authority is owned, versioned, and tied to the integration branch', () => {
  const value = contract()
  assert.equal(value.schema, 'nexus.operator-console-consolidation.v1')
  assert.equal(value.owner_issue, 747)
  assert.equal(value.parent_issue, 744)
  assert.equal(value.baseline_main_sha, '4bc76c3607db2732388d33634bc26968a880ee07')
  assert.equal(value.integration_branch, 'work/744-canonical-operator-console-consolidation')
  assert.equal(value.status, 'in_progress')
  assert.equal(existsSync(PLAN_PATH), true, 'the executable consolidation plan must exist')
})

test('there is exactly one canonical operator product spine and route', () => {
  const value = contract()
  assert.equal(value.product.canonical_product_spine, 'case_resolution')
  assert.equal(value.product.canonical_operator_route, '/workspace')

  const canonicalOperatorRoutes = value.route_authority.filter(
    (item) => item.domain === 'operator_work' && item.status === 'canonical',
  )
  assert.deepEqual(canonicalOperatorRoutes.map((item) => item.route), ['/workspace'])

  const routes = byRoute(value.route_authority)
  assert.equal(routes.get('/webchat')?.status, 'transitional_redirect_required')
  for (const route of ['/knowledge', '/channels', '/runtime', '/control-tower']) {
    assert.ok(routes.has(route), `missing target canonical route: ${route}`)
  }
})

test('new unowned route files cannot create another product spine', () => {
  const value = contract()
  const allowed = new Set(value.allowed_route_files)
  const actual = readdirSync(ROUTES_DIR)
    .filter((name) => name.endsWith('.tsx'))
    .sort()

  const unknown = actual.filter((name) => !allowed.has(name))
  assert.deepEqual(unknown, [], `unowned route files found: ${unknown.join(', ')}`)

  const router = read(join(WEBAPP_ROOT, 'src', 'router.tsx'))
  const importedRoutes = [...router.matchAll(/from ['"]@\/routes\/([^'"]+)['"]/g)].map((match) => `${match[1]}.tsx`)
  const unknownImports = importedRoutes.filter((name) => !allowed.has(name))
  assert.deepEqual(unknownImports, [], `router imports an unowned route: ${unknownImports.join(', ')}`)
})

test('all current operator surfaces have explicit dispositions', () => {
  const surfaces = byId(contract().implementation_surfaces)
  assert.equal(surfaces.get('modern_operator_workspace')?.disposition, 'CANONICAL')
  assert.equal(surfaces.get('modern_support_console')?.disposition, 'LEGACY_ACTIVE_MIGRATE_THEN_DELETE')
  assert.equal(surfaces.get('legacy_static_admin')?.disposition, 'LEGACY_ACTIVE_MIGRATE_THEN_DELETE')
  assert.equal(surfaces.get('public_webchat_widget')?.disposition, 'SEPARATE_PUBLIC_SURFACE')
  assert.equal(surfaces.size, 4, 'a new surface requires an explicit authority decision')
})

test('transport duplication is frozen and must converge on one target', () => {
  const transport = contract().transport_authority
  assert.equal(transport.target, 'webapp/src/lib/http/httpClient.ts')
  assert.deepEqual([...transport.current_duplicates].sort(), [
    'webapp/src/lib/api.ts',
    'webapp/src/lib/operatorWorkspaceApi.ts',
    'webapp/src/lib/supportApi.ts',
  ])
  for (const path of transport.current_duplicates) {
    assert.equal(existsSync(join(REPO_ROOT, path)), true, `recorded transport source is missing: ${path}`)
  }
  assert.ok(transport.required_shared_behavior.includes('auth_expiry'))
  assert.ok(transport.required_shared_behavior.includes('error_normalization'))
})

test('login presentation is operational rather than promotional or AI-styled', () => {
  const login = read(join(WEBAPP_ROOT, 'src', 'routes', 'login.tsx'))
  const authCss = read(join(WEBAPP_ROOT, 'src', 'styles', 'auth.css'))

  assert.match(login, /客服与运营工作台/)
  assert.match(login, /可见国家、渠道和操作权限由当前账号决定/)
  assert.doesNotMatch(login, /从可信事实到可验证结案/)
  assert.doesNotMatch(login, /auth-sequence/)
  assert.doesNotMatch(authCss, /auth-sequence/)
})

test('destructive retirement remains fail closed until parity and verification exist', () => {
  const value = contract()
  for (const required of [
    'consumer_inventory',
    'route_and_capability_parity',
    'authorization_preserved',
    'keyboard_and_accessibility_evidence',
    'degraded_and_large_list_evidence',
    'build_and_deployment_identity',
    'rollback_proof',
    'anti_reintroduction_gate',
    'exact_head_ci',
  ]) {
    assert.ok(value.deletion_gate.includes(required), `missing deletion prerequisite: ${required}`)
  }
  assert.ok(value.forbidden_end_state.includes('second_operator_product_spine'))
  assert.ok(value.forbidden_end_state.includes('second_production_frontend'))
  assert.ok(value.forbidden_end_state.includes('technical_status_as_business_closure'))
})
