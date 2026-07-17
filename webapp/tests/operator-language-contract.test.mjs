import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

const webapp = resolve(process.cwd())
const repo = resolve(webapp, '..')
const value = JSON.parse(readFileSync(join(webapp, 'design/operator-language.v1.json'), 'utf8'))

test('operator language is concise and exact-head verified', () => {
  assert.equal(value.schema, 'nexus.operator-language.v1')
  assert.equal(value.status, 'exact_head_verification_complete')
  for (const term of ['待处理任务', '当前负责人', '处理时限', '接手处理', '转回待处理', '恢复自动回复', '系统状态']) assert.ok(Object.values(value.canonical_terms).includes(term))
})

test('completed operator surfaces contain no retired primary literal', () => {
  for (const path of value.completed_surfaces) {
    assert.equal(existsSync(join(repo, path)), true, path)
    const source = readFileSync(join(repo, path), 'utf8')
    for (const literal of value.forbidden_primary_literals) assert.equal(source.includes(literal), false, `${path}: ${literal}`)
  }
})
