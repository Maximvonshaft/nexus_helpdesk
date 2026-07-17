import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const webapp = resolve(process.cwd())
const repo = resolve(webapp, '..')
const value = JSON.parse(readFileSync(join(webapp, 'design/ui-visual-inventory.v1.json'), 'utf8'))

test('visual inventory is source-converged', () => {
  assert.equal(value.schema, 'nexus.ui-visual-inventory.v1')
  assert.equal(value.decision.status, 'exact_head_verification_complete')
  for (const path of value.active_authorities) assert.equal(existsSync(join(repo, path)), true, path)
  for (const path of value.retired_paths) assert.equal(existsSync(join(repo, path)), false, path)
  for (const result of Object.values(value.non_duplication)) assert.equal(result, false)
})

test('Knowledge and Workspace each have one implementation graph', () => {
  assert.equal(value.implementation_graph.knowledge.second_page, false)
  assert.equal(existsSync(join(repo, value.implementation_graph.knowledge.page)), true)
  const workspace = value.implementation_graph.workspace
  for (const key of ['second_store', 'second_renderer', 'second_api']) assert.equal(workspace[key], false)
  for (const path of Object.values(workspace).filter((item) => typeof item === 'string')) assert.equal(existsSync(join(repo, path)), true, path)
})
