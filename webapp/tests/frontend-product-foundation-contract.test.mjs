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
  language: join(WEBAPP_ROOT, 'design', 'operator-language.v1.json'),
  engineering: join(REPO_ROOT, 'docs', 'engineering', 'frontend-product-foundation.md'),
  theme: join(WEBAPP_ROOT, 'src', 'theme', 'nexusTheme.ts'),
  provider: join(WEBAPP_ROOT, 'src', 'theme', 'NexusThemeProvider.tsx'),
}

const RETIRED_PATHS = [
  join(WEBAPP_ROOT, 'src', 'styles', 'tokens.css'),
  join(WEBAPP_ROOT, 'src', 'styles', 'components.css'),
  join(WEBAPP_ROOT, 'src', 'components', 'ui'),
  join(WEBAPP_ROOT, 'src', 'styles', 'auth.css'),
  join(WEBAPP_ROOT, 'src', 'app', 'app-shell.css'),
  join(WEBAPP_ROOT, 'src', 'features', 'operator-workspace', 'operator-workspace.css'),
  join(WEBAPP_ROOT, 'src', 'features', 'operator-workspace', 'operator-workspace-refinements.css'),
  join(WEBAPP_ROOT, 'src', 'features', 'admin-routes', 'admin-routes.css'),
  join(WEBAPP_ROOT, 'src', 'features', 'knowledge', 'knowledge.css'),
  join(WEBAPP_ROOT, 'src', 'features', 'runtime', 'runtime-evidence-audit.css'),
]

function readRequired(path, label) {
  assert.equal(existsSync(path), true, `${label} authority is missing: ${path}`)
  return readFileSync(path, 'utf8')
}

function parseContract() {
  return JSON.parse(readRequired(PATHS.contract, 'machine-readable frontend foundation'))
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

test('required product, design, MUI and language authorities exist', () => {
  for (const [label, path] of Object.entries(PATHS)) {
    assert.equal(existsSync(path), true, `${label} path does not exist: ${path}`)
  }
  for (const path of RETIRED_PATHS) {
    assert.equal(existsSync(path), false, `retired custom visual authority still exists: ${path}`)
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
    'operator_language_authority',
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

test('route IA centers the active operator workspace and separates administration domains', () => {
  const contract = parseContract()
  const routes = contract.route_domains.map((item) => item.route)
  assert.equal(unique(routes), true, 'route domains must be unique')

  const routeByPath = routeMap(contract)
  for (const route of ['/login', '/workspace', '/knowledge', '/channels', '/runtime', '/control-tower']) {
    assert.ok(routeByPath.has(route), `missing canonical route domain: ${route}`)
    assert.equal(routeByPath.get(route).canonical, true)
    assert.equal(routeByPath.get(route).status, 'current')
  }
  assert.equal(routeByPath.get('/workspace').domain, 'operator_work')
  assert.equal(routeByPath.get('/webchat')?.canonical, false)
  assert.equal(routeByPath.get('/webchat')?.status, 'compatibility')
})

test('MUI theme and component library are the sole generic visual authority', () => {
  const contract = parseContract()
  assert.equal(contract.token_authority.framework, 'Material UI')
  assert.equal(contract.token_authority.component_package, '@mui/material@9.2.0')
  assert.equal(contract.token_authority.icon_package, '@mui/icons-material@9.2.0')
  assert.equal(contract.token_authority.styling_engine, 'Emotion')
  assert.equal(contract.token_authority.theme_path, 'webapp/src/theme/nexusTheme.ts')
  assert.equal(contract.token_authority.provider_path, 'webapp/src/theme/NexusThemeProvider.tsx')
  assert.equal(contract.token_authority.baseline, 'CssBaseline')
  assert.equal(contract.token_authority.generic_custom_component_policy, 'prohibited')
  assert.equal(contract.token_authority.route_visual_css_policy, 'prohibited')
  assert.deepEqual(contract.token_authority.allowed_global_css.sort(), [
    'webapp/src/a11y.css',
    'webapp/src/styles.css',
  ])
  assert.ok(contract.token_authority.retired_authorities.includes('webapp/src/components/ui'))
  assert.ok(contract.token_authority.retired_authorities.includes('webapp/src/styles/tokens.css'))
})

test('operator language is a single bounded authority', () => {
  const contract = parseContract()
  const language = JSON.parse(readRequired(PATHS.language, 'operator language authority'))
  assert.equal(contract.operator_language_authority.path, 'webapp/design/operator-language.v1.json')
  assert.equal(contract.operator_language_authority.status, language.status)
  assert.deepEqual(contract.operator_language_authority.primary_surface_content, [
    'task',
    'state',
    'action',
    'blocking reason',
    'recovery step',
  ])
  assert.equal(contract.operator_language_authority.technical_disclosure_only, true)
  assert.ok(contract.operator_language_authority.primary_surface_forbidden.includes('product narration'))
  assert.ok(contract.operator_language_authority.primary_surface_forbidden.includes('AI self-description'))
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

test('terminology blocks false closure, long-term-memory and narrative platform language', () => {
  const contract = parseContract()
  assert.ok(contract.terminology.prohibited_operator_labels.includes('记忆证据'))
  assert.ok(contract.terminology.prohibited_operator_labels.includes('已结束'))
  assert.ok(contract.terminology.prohibited_operator_labels.includes('服务端最终授权'))
  assert.ok(contract.terminology.prohibited_operator_labels.includes('运营中心'))
  assert.ok(contract.terminology.false_success_sources.includes('http_200'))
  assert.ok(contract.terminology.false_success_sources.includes('job_done'))
  assert.ok(contract.terminology.false_success_sources.includes('message_sent'))
  assert.ok(contract.terminology.false_success_sources.includes('dispatch_dispatched'))
  assert.ok(contract.terminology.preferred_evidence_labels.includes('已知信息'))
  assert.ok(contract.terminology.preferred_evidence_labels.includes('处理决定'))
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

test('engineering integration records the active MUI replacement authority', () => {
  const guide = readRequired(PATHS.engineering, 'frontend engineering integration guide')
  const normalizedGuide = guide.toLowerCase()
  for (const commitment of [
    '#748',
    '#753',
    '@mui/material',
    'webapp/src/theme/nexusTheme.ts',
    'webapp/src/theme/NexusThemeProvider.tsx',
    'webapp/src/domain/operationalPresentation.ts',
  ]) {
    assert.ok(normalizedGuide.includes(commitment.toLowerCase()), `engineering guide missing active authority: ${commitment}`)
  }
  assert.ok(normalizedGuide.includes('no parallel implementation'))
  assert.ok(normalizedGuide.includes('github actions are retired'))
})

test('visual migration is complete while language convergence and production acceptance remain pending', () => {
  const contract = parseContract()
  assert.equal(contract.lifecycle.status, 'mui_code_migration_complete_language_convergence_in_progress_verification_pending')
  assert.equal(contract.lifecycle.runtime_activation, false)
  assert.equal(contract.lifecycle.production_ui_migration_complete, false)
  assert.deepEqual(contract.downstream_work_items, [525, 564, 573, 753])
})
