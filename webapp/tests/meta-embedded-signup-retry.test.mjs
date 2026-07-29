import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../src/lib/metaEmbeddedSignup.ts', import.meta.url),
  'utf8',
)

test('Meta SDK retry removes stale script nodes after timeout or load error', () => {
  assert.match(source, /document\.getElementById\(SDK_ID\)\?\.remove\(\)/)
  assert.match(source, /resetMetaSdkLoad\('meta_sdk_load_timeout'\)/)
  assert.match(source, /resetMetaSdkLoad\('meta_sdk_load_failed'\)/)
  assert.doesNotMatch(source, /if \(existing\) return/)
})
