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
  presentation: join(WEBAPP_ROOT, 'src', 'app', 'OperatorPresentation.tsx'),
}

const RETIRED_PATHS = [
  'src/styles/tokens.css',
  'src/styles/components.css',
  'src/components/ui',
  'src/styles/auth.css',
  'src/app/app-shell.css',
  'src/features/operator-workspace/operator-workspace.css',
  'src/features/operator-workspace/operator-workspace-refinements.css',
  'src/features/admin-routes/admin-routes.css',
  'src/features/knowledge/knowledge.css',
  'src/features/knowledge/KnowledgeReadOnlyPage.tsx',
  'src/features/runtime/runtime-evidence-audit.css',
]

function readRequired(path, label) {
  assert.equal(existsSync(path), true, `${label} authority is missing: ${path}`)
  return readFileSync(path, 'utf8')
}

function parseContract() {
  return JSON.parse(readRequired(PATHS.contract, 'frontend foundation'))
}

test('product, design, MUI and operator-language authorities exist', () => {
  for (const [label, path] of Object.entries(PATHS)) assert.equal(existsSync(path), true, `${label} path is missing`)
  for (const path of RETIRED_PATHS) assert.equal(existsSync(join(WEBAPP_ROOT, path)), false, `retired visual authority returned: ${path}`)
})

test('foundation is versioned and route ownership is unique', () => {
  const contract = parseContract()
  assert.equal(contract.schema, 'nexus.frontend-product-foundation.v1')
  assert.equal(contract.version, 'frontend_product_foundation.v2')
  const routes = contract.route_domains.map((item) => item.route)
  assert.equal(new Set(routes).size, routes.length)
  for (const route of ['/login', '/workspace', '/knowledge', '/channels', '/runtime', '/control-tower']) {
    const domain = contract.route_domains.find((item) => item.route === route)
    assert.equal(domain?.canonical, true, `route is not canonical: ${route}`)
    assert.equal(domain?.status, 'current')
  }
  const compatibility = contract.route_domains.find((item) => item.route === '/webchat')
  assert.equal(compatibility?.canonical, false)
  assert.equal(compatibility?.status, 'compatibility')
})

test('MUI theme and operator presentation are the sole generic visual authorities', () => {
  const authority = parseContract().token_authority
  assert.equal(authority.framework, 'Material UI')
  assert.equal(authority.component_package, '@mui/material@9.2.0')
  assert.equal(authority.icon_package, '@mui/icons-material@9.2.0')
  assert.equal(authority.styling_engine, 'Emotion')
  assert.equal(authority.theme_path, 'webapp/src/theme/nexusTheme.ts')
  assert.equal(authority.provider_path, 'webapp/src/theme/NexusThemeProvider.tsx')
  assert.equal(authority.operator_presentation_path, 'webapp/src/app/OperatorPresentation.tsx')
  assert.equal(authority.generic_custom_component_policy, 'prohibited')
  assert.equal(authority.route_visual_css_policy, 'prohibited')
  assert.deepEqual(authority.allowed_global_css.sort(), ['webapp/src/a11y.css', 'webapp/src/styles.css'])
})

test('Knowledge and Workspace each have one implementation graph', () => {
  const authorities = parseContract().implementation_authorities
  assert.equal(authorities.knowledge.mode, 'canManage')
  assert.equal(authorities.knowledge.second_page, false)
  assert.equal(authorities.workspace.second_store, false)
  assert.equal(authorities.workspace.second_renderer, false)
  assert.equal(authorities.workspace.second_api, false)
  for (const path of [authorities.knowledge.route, authorities.knowledge.page, ...Object.values(authorities.workspace).filter((item) => typeof item === 'string' && item.startsWith('webapp/'))]) {
    assert.equal(existsSync(join(REPO_ROOT, path)), true, path)
  }
})

test('operator language convergence is complete in code and remains verification-gated', () => {
  const contract = parseContract()
  const language = JSON.parse(readRequired(PATHS.language, 'operator language'))
  assert.equal(contract.operator_language_authority.path, 'webapp/design/operator-language.v1.json')
  assert.equal(language.status, 'code_convergence_complete_verification_pending')
  assert.deepEqual(language.pending_surfaces, [])
  assert.equal(contract.operator_language_authority.technical_disclosure_only, true)
  assert.equal(contract.lifecycle.status, 'mui_operator_language_and_source_convergence_complete_verification_pending')
  assert.equal(contract.lifecycle.production_ui_migration_complete, false)
})

test('state vocabulary does not collapse technical activity into safe closure', () => {
  const contract = parseContract()
  const states = Object.values(contract.state_vocabulary).flat()
  for (const required of ['source_closed', 'evidence_authoritative', 'evidence_customer_claim', 'action_requested', 'action_technical_completed', 'action_operational_completed', 'customer_notified', 'business_result_confirmed', 'repair_required', 'closure_observation', 'closure_safely_closed', 'closure_reopened']) assert.ok(states.includes(required), `missing state: ${required}`)
  assert.ok(contract.terminology.false_success_sources.includes('http_200'))
  assert.ok(contract.terminology.false_success_sources.includes('job_done'))
  assert.ok(contract.terminology.preferred_evidence_labels.includes('已知信息'))
})

test('quality floor remains accessibility and responsive complete', () => {
  const quality = parseContract().quality_floor
  assert.equal(quality.wcag_level, 'AA')
  assert.equal(quality.normal_text_contrast_ratio, 4.5)
  assert.equal(quality.minimum_target_css_px, 44)
  assert.equal(quality.reduced_motion_required, true)
  assert.equal(quality.keyboard_journey_required, true)
  assert.equal(quality.slow_network_required, true)
  assert.equal(quality.large_list_required, true)
  assert.deepEqual(quality.representative_viewports, [375, 768, 1024, 1440])
})

test('product and design registers preserve the Nexus-specific case journey', () => {
  const product = readRequired(PATHS.product, 'PRODUCT.md')
  const design = readRequired(PATHS.design, 'DESIGN.md')
  for (const phrase of ['case-resolution cockpit', 'authoritative evidence', 'business result', 'observation', 'reopen', '/workspace']) assert.ok(product.includes(phrase), `PRODUCT.md missing ${phrase}`)
  for (const phrase of ['Dense calm logistics cockpit', 'Case Spine', '44×44', 'WCAG AA', 'prefers-reduced-motion', 'No endless card grids']) assert.ok(design.includes(phrase), `DESIGN.md missing ${phrase}`)
})

test('engineering integration records the active non-duplicate authority', () => {
  const guide = readRequired(PATHS.engineering, 'frontend engineering guide').toLowerCase()
  for (const phrase of ['#748', '#753', '@mui/material', 'nexustheme.ts', 'nexusthemeprovider.tsx', 'no parallel implementation', 'github actions are retired']) assert.ok(guide.includes(phrase.toLowerCase()), `engineering guide missing ${phrase}`)
})
