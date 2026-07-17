import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const webapp = resolve(process.cwd())
const repo = resolve(webapp, '..')
const value = JSON.parse(readFileSync(join(webapp, 'design/frontend-product-foundation.v1.json'), 'utf8'))

test('frontend foundation has one exact-head authority graph', () => {
  assert.equal(value.schema, 'nexus.frontend-product-foundation.v1')
  assert.equal(value.status, 'exact_head_verified_ready_to_merge')
  for (const path of Object.values(value.authorities).filter((item) => typeof item === 'string' && item.startsWith('webapp/'))) assert.equal(existsSync(join(repo, path)), true, path)
  assert.equal(new Set(value.routes.map((route) => route.path)).size, value.routes.length)
  for (const result of Object.values(value.invariants)) assert.equal(result, true)
})

test('retired frontend authorities remain absent', () => {
  for (const path of value.retired_paths) assert.equal(existsSync(join(repo, path)), false, path)
  assert.equal(value.verification.github_actions, 'retired_and_absent')
  assert.equal(value.deployment_authorized, false)
})
