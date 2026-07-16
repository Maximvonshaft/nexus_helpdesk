import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const REPO_ROOT = resolve(WEBAPP_ROOT, '..')
const authorityPath = join(WEBAPP_ROOT, 'design', 'mui-visual-authority.v1.json')

function authority() {
  assert.equal(existsSync(authorityPath), true, 'MUI visual authority contract is missing')
  return JSON.parse(readFileSync(authorityPath, 'utf8'))
}

test('MUI is the single authorized replacement visual framework', () => {
  const contract = authority()
  assert.equal(contract.schema, 'nexus.mui-visual-authority.v1')
  assert.equal(contract.work_item, 753)
  assert.equal(contract.decision.status, 'code_migration_complete_verification_pending')
  assert.equal(contract.decision.owner_authorized, true)
  assert.equal(contract.decision.selected_framework, 'Material UI')
  assert.equal(contract.decision.selected_package, '@mui/material')
  assert.equal(contract.decision.selected_version, '9.2.0')
  assert.equal(contract.decision.license, 'MIT')
})

test('the exact React 18 compatible MUI package set is bounded', () => {
  const contract = authority()
  assert.deepEqual(contract.runtime_packages, {
    '@mui/material': '9.2.0',
    '@mui/icons-material': '9.2.0',
    '@emotion/react': '11.14.0',
    '@emotion/styled': '11.14.1',
    'react-is': '18.3.1',
  })
  assert.equal(contract.react_compatibility.react, '18.3.1')
  assert.equal(contract.react_compatibility.react_dom, '18.3.1')
  assert.equal(contract.react_compatibility.react_is_override_required, true)
  assert.equal(contract.react_compatibility.react_is_override, '18.3.1')
})

test('one MUI theme and direct MUI component usage own generic presentation', () => {
  const contract = authority()
  assert.equal(contract.target_authority.component_library, '@mui/material')
  assert.equal(contract.target_authority.styling_engine, 'Emotion')
  assert.equal(contract.target_authority.theme_provider, 'ThemeProvider')
  assert.equal(contract.target_authority.baseline, 'CssBaseline')
  assert.equal(contract.target_authority.component_usage, 'direct MUI imports for generic controls; Nexus components only for domain-specific composition')
  assert.equal(existsSync(join(WEBAPP_ROOT, 'src', 'theme', 'nexusTheme.ts')), true)
  assert.equal(existsSync(join(WEBAPP_ROOT, 'src', 'theme', 'NexusThemeProvider.tsx')), true)
  assert.equal(existsSync(join(WEBAPP_ROOT, 'src', 'components', 'ui')), false)
})

test('the completed code migration physically retired the old visual system', () => {
  const contract = authority()
  const state = contract.implementation_state
  for (const key of [
    'single_theme_created',
    'root_provider_mounted',
    'login_migrated',
    'application_shell_migrated',
    'workspace_migrated',
    'knowledge_migrated',
    'channels_migrated',
    'runtime_migrated',
    'runtime_evidence_audit_migrated',
    'control_tower_migrated',
    'boundary_pages_migrated',
    'custom_generic_components_deleted',
    'radix_dialog_removed',
    'custom_token_system_deleted',
    'route_visual_css_deleted',
    'legacy_visual_residue_deleted',
  ]) {
    assert.equal(state[key], true, `MUI migration state is incomplete: ${key}`)
  }
  assert.equal(state.package_lock_regenerated, false)
  assert.equal(state.local_verification_completed, false)
  assert.equal(state.browser_acceptance_completed, false)

  for (const path of [
    ...contract.retirement_evidence.deleted_generic_components,
    ...contract.retirement_evidence.deleted_visual_css,
  ]) {
    assert.equal(existsSync(join(REPO_ROOT, path)), false, `retired visual path returned: ${path}`)
  }
})

test('migration cannot merge without exact lock, tests and browser evidence', () => {
  const contract = authority()
  assert.equal(contract.migration_policy.branch, 'work/753-canonical-ui-interaction-refinement')
  assert.equal(contract.migration_policy.single_pr, 754)
  assert.equal(contract.migration_policy.partial_merge_forbidden, true)
  assert.equal(contract.migration_policy.old_new_runtime_switch_forbidden, true)
  assert.equal(contract.migration_policy.v2_routes_forbidden, true)
  assert.equal(contract.migration_policy.parallel_framework_forbidden, true)
  assert.match(contract.migration_policy.merge_condition, /package-lock\.json/)
  assert.match(contract.migration_policy.merge_condition, /architecture, lint, typecheck, tests, build, browser acceptance and independent review/)
})

test('the target preserves behavior and accessibility while replacing appearance', () => {
  const contract = authority()
  for (const responsibility of [
    'backend APIs',
    'authorization',
    'queue truth',
    'business state contracts',
    'draft protection',
    'confirmation requirements',
    'mutation safety',
  ]) {
    assert.ok(contract.preserve.includes(responsibility), `missing preserved responsibility: ${responsibility}`)
  }
  assert.equal(contract.acceptance.mui_is_only_generic_visual_component_authority, true)
  assert.equal(contract.acceptance.no_custom_generic_button_field_dialog_badge_system, true)
  assert.equal(contract.acceptance.no_legacy_css_after_merge, true)
  assert.equal(contract.acceptance.wcag_aa, true)
  assert.equal(contract.acceptance.minimum_target_css_px, 44)
  assert.deepEqual(contract.acceptance.representative_viewports, [375, 768, 1024, 1440])
})
