import test from 'node:test'
import assert from 'node:assert/strict'

test('webapp test harness is active', () => {
  assert.equal(typeof test, 'function')
})
