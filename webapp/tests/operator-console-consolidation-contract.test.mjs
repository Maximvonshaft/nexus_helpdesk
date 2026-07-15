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
  assert.equal(value.baseline_main_sha, '7ffdbf5941853b4c70d0ec0c2ef0a02cfaa60498')
  assert.equal(value.integration_branch, 'work/744-canonical-operator-console-consolidation')
  assert.equal(value.status, 'code_converged_local_verification_required')
  assert.equal(existsSync(PLAN_PATH), true, 'the executable consolidation plan must exist')
  assert.equal(value.delivery_authority.forbidden.includes('second_github_actions_workflow'), true)
  assert.equal(value.verification.github_actions, 'one_canonical_verification_workflow')
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
  assert.equal(routes.get('/webchat')?.status, 'redirect_only')
  for (const route of ['/knowledge', '/channels', '/runtime', '/control-tower']) {
    assert.equal(routes.get(route)?.status, 'canonical', `route is not canonical: ${route}`)
  }
})

test('new unowned route files cannot create another product spine', () => {
  const value = contract()
  const allowed = new Set(value.allowed_route_files)
  const actual = readdirSync(ROUTES_DIR).filter((name) => name.endsWith('.tsx')).sort()
  const unknown = actual.filter((name) => !allowed.has(name))
  assert.deepEqual(unknown, [], `unowned route files found: ${unknown.join(', ')}`)
  const router = read(join(WEBAPP_ROOT, 'src', 'router.tsx'))
  const importedRoutes = [...router.matchAll(/from ['"]@\/routes\/([^'"]+)['"]/g)].map((match) => `${match[1]}.tsx`)
  const unknownImports = importedRoutes.filter((name) => !allowed.has(name))
  assert.deepEqual(unknownImports, [], `router imports an unowned route: ${unknownImports.join(', ')}`)
})

test('all current and retired operator surfaces have explicit tracked-tree dispositions', () => {
  const value = contract()
  for (const path of value.retired_surfaces) {
    assert.equal(existsSync(join(REPO_ROOT, path)), false, `retired surface still exists: ${path}`)
  }
  for (const path of Object.values(value.canonical_authorities)) {
    assert.equal(existsSync(join(REPO_ROOT, path)), true, `canonical authority is missing: ${path}`)
  }
  assert.equal(existsSync(join(WEBAPP_ROOT, 'src', 'features', 'support-console')), false)
  assert.equal(existsSync(join(REPO_ROOT, 'frontend')), false)
})

test('one canonical transport owns fetch and domain adapters only delegate', () => {
  const transport = contract().transport_authority
  assert.equal(transport.target, 'webapp/src/lib/apiClient.ts')
  assert.deepEqual(transport.current_duplicates, [])
  assert.equal(existsSync(join(REPO_ROOT, transport.target)), true)
  assert.equal(existsSync(join(REPO_ROOT, 'webapp/src/lib/api.ts')), false)

  const authority = read(join(REPO_ROOT, transport.target))
  assert.match(authority, /export async function apiRequest/)
  assert.match(authority, /SAFE_RETRY_METHODS/)
  assert.match(authority, /nexusdesk:api-latency/)
  assert.match(authority, /externalSignal/)

  for (const path of transport.delegating_adapters) {
    const source = read(join(REPO_ROOT, path))
    assert.match(source, /apiRequest/)
    assert.doesNotMatch(source, /\bfetch\s*\(/, `domain adapter owns fetch: ${path}`)
    assert.doesNotMatch(source, /new AbortController\s*\(/, `domain adapter owns timeout: ${path}`)
  }

  assert.ok(transport.required_shared_behavior.includes('auth_expiry'))
  assert.ok(transport.required_shared_behavior.includes('error_normalization'))
  assert.ok(transport.required_shared_behavior.includes('external_abort_propagation'))
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
  const verifier = read(join(REPO_ROOT, value.canonical_authorities.local_verification))
  for (const path of value.retired_surfaces) {
    assert.equal(existsSync(join(REPO_ROOT, path)), false, `retired surface returned: ${path}`)
    assert.match(verifier, new RegExp(path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.ok(value.forbidden_end_state.includes('second_operator_product_spine'))
  assert.ok(value.forbidden_end_state.includes('second_production_frontend'))
  assert.ok(value.forbidden_end_state.includes('technical_status_as_business_closure'))
})
