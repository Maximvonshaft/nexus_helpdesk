import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const authorityPath = join(WEBAPP_ROOT, 'design', 'mui-visual-authority.v1.json')

function authority() {
  assert.equal(existsSync(authorityPath), true, 'MUI visual authority contract is missing')
  return JSON.parse(readFileSync(authorityPath, 'utf8'))
}

test('MUI is the single authorized replacement visual framework', () => {
  const contract = authority()
  assert.equal(contract.schema, 'nexus.mui-visual-authority.v1')
  assert.equal(contract.work_item, 753)
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

test('MUI replaces generic controls rather than coexisting with them', () => {
  const contract = authority()
  assert.equal(contract.target_authority.component_library, '@mui/material')
  assert.equal(contract.target_authority.styling_engine, 'Emotion')
  assert.equal(contract.target_authority.theme_provider, 'ThemeProvider')
  assert.equal(contract.target_authority.baseline, 'CssBaseline')
  assert.equal(contract.target_authority.component_usage, 'direct MUI imports for generic controls; Nexus components only for domain-specific composition')
  assert.ok(contract.replacement_scope.generic_components_to_replace_and_delete.length >= 9)
  assert.ok(contract.replacement_scope.css_to_migrate_and_delete.length >= 9)
  assert.deepEqual(contract.replacement_scope.interaction_dependency_to_remove, ['@radix-ui/react-dialog'])
})

test('migration cannot merge with two visual systems', () => {
  const contract = authority()
  assert.equal(contract.migration_policy.branch, 'work/753-canonical-ui-interaction-refinement')
  assert.equal(contract.migration_policy.single_pr, 754)
  assert.equal(contract.migration_policy.partial_merge_forbidden, true)
  assert.equal(contract.migration_policy.old_new_runtime_switch_forbidden, true)
  assert.equal(contract.migration_policy.v2_routes_forbidden, true)
  assert.equal(contract.migration_policy.parallel_framework_forbidden, true)
  assert.match(contract.migration_policy.merge_condition, /all active routes use MUI/)
  assert.match(contract.migration_policy.merge_condition, /deleted/)
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
