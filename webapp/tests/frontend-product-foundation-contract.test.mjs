import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const REPO_ROOT = resolve(WEBAPP_ROOT, '..')

const PATHS = {
  product: join(WEBAPP_ROOT, 'PRODUCT.md'),
  design: join(WEBAPP_ROOT, 'DESIGN.md'),
  contract: join(WEBAPP_ROOT, 'design', 'frontend-product-foundation.v1.json'),
  engineering: join(REPO_ROOT, 'docs', 'engineering', 'frontend-product-foundation.md'),
  tokens: join(WEBAPP_ROOT, 'src', 'styles', 'tokens.css'),
  components: join(WEBAPP_ROOT, 'src', 'components', 'ui'),
}

function readRequired(path, label) {
  assert.equal(existsSync(path), true, `${label} authority is missing: ${path}`)
  return readFileSync(path, 'utf8')
}

function parseContract() {
  const raw = readRequired(PATHS.contract, 'machine-readable frontend foundation')
  return JSON.parse(raw)
}

function unique(values) {
  return new Set(values).size === values.length
}

function routeMap(contract) {
  return new Map(contract.route_domains.map((item) => [item.route, item]))
}

function flattenStateVocabulary(contract) {
  return Object.values(contract.state_vocabulary).flat()
}

test('required product and design authorities exist', () => {
  for (const [label, path] of Object.entries(PATHS)) {
    assert.equal(existsSync(path), true, `${label} path does not exist: ${path}`)
  }
})

test('machine-readable foundation is versioned and bounded', () => {
  const contract = parseContract()
  assert.equal(contract.schema, 'nexus.frontend-product-foundation.v1')
  assert.match(contract.version, /^frontend_product_foundation\.v\d+$/)
  assert.equal(contract.owner, 'nexus_osr_product_design')
  assert.deepEqual(Object.keys(contract).sort(), [
    'downstream_work_items',
    'lifecycle',
    'owner',
    'product_job',
    'quality_floor',
    'route_domains',
    'schema',
    'signature',
    'state_vocabulary',
    'terminology',
    'token_authority',
    'version',
    'visual_thesis',
  ].sort())
})

test('route IA centers the operator workspace and separates administration domains', () => {
  const contract = parseContract()
  const routes = contract.route_domains.map((item) => item.route)
  assert.equal(unique(routes), true, 'route domains must be unique')

  const routeByPath = routeMap(contract)
  for (const route of ['/login', '/workspace', '/knowledge', '/channels', '/runtime', '/control-tower']) {
    assert.ok(routeByPath.has(route), `missing canonical route domain: ${route}`)
  }
  assert.equal(routeByPath.get('/workspace').domain, 'operator_work')
  assert.equal(routeByPath.get('/workspace').canonical, true)
  assert.equal(routeByPath.get('/webchat')?.canonical, false)
  assert.equal(routeByPath.get('/webchat')?.status, 'transitional')
})

test('one semantic token and component authority is declared', () => {
  const contract = parseContract()
  assert.equal(contract.token_authority.semantic_tokens_path, 'webapp/src/styles/tokens.css')
  assert.equal(contract.token_authority.component_primitives_path, 'webapp/src/components/ui')
  assert.equal(contract.token_authority.feature_raw_hex_policy, 'prohibited_after_migration')
  assert.deepEqual(contract.token_authority.legacy_sources.sort(), [
    'webapp/src/features/support-console/support-console.css',
    'webapp/src/styles.css',
  ].sort())
})

test('state vocabulary does not collapse technical activity into business closure', () => {
  const contract = parseContract()
  const states = flattenStateVocabulary(contract)
  for (const required of [
    'source_closed',
    'evidence_authoritative',
    'evidence_customer_claim',
    'action_requested',
    'action_technical_completed',
    'action_operational_completed',
    'customer_notified',
    'business_result_confirmed',
    'repair_required',
    'closure_observation',
    'closure_safely_closed',
    'closure_reopened',
  ]) {
    assert.ok(states.includes(required), `missing state vocabulary: ${required}`)
  }
  assert.notEqual('source_closed', 'closure_safely_closed')
})

test('quality floor encodes accessibility, touch, responsive and reduced-motion rules', () => {
  const contract = parseContract()
  assert.equal(contract.quality_floor.normal_text_contrast_ratio, 4.5)
  assert.equal(contract.quality_floor.minimum_target_css_px, 44)
  assert.equal(contract.quality_floor.reduced_motion_required, true)
  assert.deepEqual(contract.quality_floor.representative_viewports, [375, 768, 1024, 1440])
  assert.equal(contract.quality_floor.keyboard_journey_required, true)
  assert.equal(contract.quality_floor.slow_network_required, true)
  assert.equal(contract.quality_floor.large_list_required, true)
})

test('terminology blocks false closure and long-term-memory language', () => {
  const contract = parseContract()
  assert.ok(contract.terminology.prohibited_operator_labels.includes('记忆证据'))
  assert.ok(contract.terminology.prohibited_operator_labels.includes('已结束'))
  assert.ok(contract.terminology.false_success_sources.includes('http_200'))
  assert.ok(contract.terminology.false_success_sources.includes('job_done'))
  assert.ok(contract.terminology.false_success_sources.includes('message_sent'))
  assert.ok(contract.terminology.false_success_sources.includes('dispatch_dispatched'))
})

test('PRODUCT register is Nexus-specific and defines the canonical case journey', () => {
  const product = readRequired(PATHS.product, 'PRODUCT.md')
  for (const phrase of [
    'case-resolution cockpit',
    'authoritative evidence',
    'business result',
    'observation',
    'reopen',
    'No C-end long-term customer memory',
    '/workspace',
  ]) {
    assert.ok(product.includes(phrase), `PRODUCT.md missing required commitment: ${phrase}`)
  }
})

test('DESIGN register defines a subject-grounded non-template direction', () => {
  const design = readRequired(PATHS.design, 'DESIGN.md')
  for (const phrase of [
    'Dense calm logistics cockpit',
    'Case Spine',
    '44×44',
    'WCAG AA',
    'prefers-reduced-motion',
    'No generic gradient',
    'No endless card grids',
  ]) {
    assert.ok(design.includes(phrase), `DESIGN.md missing required commitment: ${phrase}`)
  }
})

test('engineering integration assigns implementation to existing authorities', () => {
  const guide = readRequired(PATHS.engineering, 'frontend engineering integration guide')
  for (const workItem of ['#525', '#564', '#573', '#587', '#526']) {
    assert.ok(guide.includes(workItem), `engineering guide must reference ${workItem}`)
  }
  assert.ok(guide.includes('no big-bang rewrite'))
  assert.ok(guide.includes('architecture gate'))
})

test('the foundation remains additive and does not claim current route implementation', () => {
  const contract = parseContract()
  assert.equal(contract.lifecycle.status, 'approved_contract')
  assert.equal(contract.lifecycle.runtime_activation, false)
  assert.equal(contract.lifecycle.production_ui_migration_complete, false)
  assert.deepEqual(contract.downstream_work_items, [525, 564, 573])
})
