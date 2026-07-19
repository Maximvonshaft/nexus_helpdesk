import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const closure = fs.readFileSync(new URL('../src/features/operator-workspace/OperatorWorkspaceClosure.tsx', import.meta.url), 'utf8')
const casePane = fs.readFileSync(new URL('../src/features/operator-workspace/OperatorWorkspaceCase.tsx', import.meta.url), 'utf8')
const api = fs.readFileSync(new URL('../src/lib/supportApi.ts', import.meta.url), 'utf8')

test('workspace consumes the canonical server closure receipt', () => {
  assert.match(casePane, /<OperatorWorkspaceClosure/)
  assert.match(closure, /supportApi\.ticketClosureReadiness/)
  assert.match(closure, /readiness\.closure_ready/)
  assert.match(closure, /latest\.receipt_sha256/)
  assert.doesNotMatch(closure, /resolution_category|ticket\.status\s*===\s*['"]resolved['"]/)
})

test('closure evidence remains source-bound and server validated', () => {
  assert.match(closure, /source_ref/)
  assert.match(closure, /source_revision/)
  assert.match(closure, /source_kind/)
  assert.match(api, /\/closure-evidence/)
  assert.match(api, /\/closure-readiness/)
})

test('operator cannot close by bypassing a stale receipt', () => {
  assert.match(closure, /const latest = await supportApi\.ticketClosureReadiness/)
  assert.match(closure, /if \(!latest\.readiness\.closure_ready\)/)
  assert.match(closure, /supportApi\.closeTicket/)
})
