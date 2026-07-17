import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const webapp = resolve(process.cwd())
const repo = resolve(webapp, '..')
const value = JSON.parse(readFileSync(join(webapp, 'design/operator-console-consolidation.v1.json'), 'utf8'))

test('operator console is one verified product spine', () => {
  assert.equal(value.schema, 'nexus.operator-console-consolidation.v1')
  assert.equal(value.status, 'exact_head_verified_ready_to_merge')
  assert.equal(new Set(value.routes).size, value.routes.length)
  for (const path of Object.values(value.authorities).filter((item) => item.startsWith('webapp/'))) assert.equal(existsSync(join(repo, path)), true, path)
  assert.match(value.verification.note, /one unchanged candidate tree/)
})
