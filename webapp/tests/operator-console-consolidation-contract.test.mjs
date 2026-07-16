import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const REPO_ROOT = resolve(WEBAPP_ROOT, '..')
const CONTRACT_PATH = join(WEBAPP_ROOT, 'design', 'operator-console-consolidation.v1.json')
const ROUTES_DIR = join(WEBAPP_ROOT, 'src', 'routes')
const read = (path) => readFileSync(path, 'utf8')
const contract = () => JSON.parse(read(CONTRACT_PATH))

test('consolidation authority is versioned and bound to the sole MUI delivery', () => {
  const value = contract()
  assert.equal(value.schema, 'nexus.operator-console-consolidation.v1')
  assert.equal(value.version, 'operator_console_consolidation.2026-07-16.3')
  assert.equal(value.owner_issue, 747)
  assert.equal(value.status, 'source_converged_mui_verification_required')
  assert.equal(value.delivery_authority.visual_replacement_pr, 754)
  assert.equal(value.delivery_authority.actions_state, 'retired')
  assert.equal(value.verification.github_actions, 'retired_and_absent')
})

test('there is exactly one canonical route per product domain', () => {
  const value = contract()
  assert.equal(value.product.canonical_operator_route, '/workspace')
  assert.deepEqual(value.route_authority.filter((item) => item.domain === 'operator_work').map((item) => item.route), ['/workspace'])
  const knowledge = value.route_authority.find((item) => item.route === '/knowledge')
  assert.equal(knowledge.implementation, 'webapp/src/features/knowledge/KnowledgePage.tsx')
  assert.equal(knowledge.capability_mode, 'canManage')
  assert.equal(value.route_authority.find((item) => item.route === '/webchat')?.status, 'redirect_only')
})

test('unowned route files cannot create another product spine', () => {
  const value = contract()
  const allowed = new Set(value.allowed_route_files)
  const actual = readdirSync(ROUTES_DIR).filter((name) => name.endsWith('.tsx')).sort()
  assert.deepEqual(actual.filter((name) => !allowed.has(name)), [])
})

test('canonical authorities exist and retired surfaces are absent', () => {
  const value = contract()
  for (const path of value.retired_surfaces) assert.equal(existsSync(join(REPO_ROOT, path)), false, path)
  for (const path of Object.values(value.canonical_authorities)) assert.equal(existsSync(join(REPO_ROOT, path)), true, path)
  assert.ok(value.completed_convergence.includes('single_operator_presentation_authority'))
  assert.ok(value.completed_convergence.includes('single_knowledge_implementation_with_capability_mode'))
  assert.ok(value.completed_convergence.includes('single_workspace_state_and_api'))
})

test('one canonical transport owns fetch and adapters delegate', () => {
  const transport = contract().transport_authority
  const authority = read(join(REPO_ROOT, transport.target))
  assert.match(authority, /export async function apiRequest/)
  assert.match(authority, /externalSignal/)
  for (const path of transport.delegating_adapters) {
    const source = read(join(REPO_ROOT, path))
    assert.match(source, /apiRequest/)
    assert.doesNotMatch(source, /\bfetch\s*\(/)
  }
})

test('login and MUI authorities are concise and singular', () => {
  const value = contract()
  const login = read(join(WEBAPP_ROOT, 'src', 'routes', 'login.tsx'))
  const theme = read(join(REPO_ROOT, value.canonical_authorities.mui_theme))
  const provider = read(join(REPO_ROOT, value.canonical_authorities.mui_provider))
  const presentation = read(join(REPO_ROOT, value.canonical_authorities.operator_presentation))
  assert.match(login, /Nexus OSR/)
  assert.match(login, /账号或密码错误。/)
  assert.doesNotMatch(login, /客服与运营工作台|从可信事实到可验证结案/)
  assert.match(theme, /createTheme/)
  assert.match(provider, /ThemeProvider/)
  assert.match(presentation, /OperatorEmptyState/)
})

test('retirement remains fail closed until exact-head verification', () => {
  const value = contract()
  assert.ok(value.forbidden_end_state.includes('second_operator_product_spine'))
  assert.ok(value.forbidden_end_state.includes('second_production_frontend'))
  assert.ok(value.forbidden_end_state.includes('second_transport_authority'))
  assert.match(value.verification.note, /dependency lock regeneration/)
})
