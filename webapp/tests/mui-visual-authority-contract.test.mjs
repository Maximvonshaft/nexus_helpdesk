import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const webapp = resolve(process.cwd())
const repo = resolve(webapp, '..')
const value = JSON.parse(readFileSync(join(webapp, 'design/mui-visual-authority.v1.json'), 'utf8'))
const manifest = JSON.parse(readFileSync(join(webapp, 'package.json'), 'utf8'))

test('MUI is the only exact visual framework authority', () => {
  assert.equal(value.schema, 'nexus.mui-visual-authority.v1')
  assert.equal(value.decision.status, 'exact_head_verification_complete')
  assert.equal(value.decision.selected_package, '@mui/material')
  assert.equal(value.decision.selected_version, '9.2.0')
  for (const [name, version] of Object.entries(value.runtime_packages)) assert.equal(manifest.dependencies[name], version)
  for (const path of Object.values(value.authorities)) assert.equal(existsSync(join(repo, path)), true, path)
})

test('old visual authorities are absent and verification is complete', () => {
  for (const path of value.retired_paths) assert.equal(existsSync(join(repo, path)), false, path)
  for (const result of Object.values(value.verification)) assert.notEqual(result, 'pending')
})
