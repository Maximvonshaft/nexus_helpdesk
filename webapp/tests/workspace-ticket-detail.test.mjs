import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const workspaceRoute = readFileSync(resolve(root, 'src/routes/workspace.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')

test('workspace ticket detail first load uses summary endpoint instead of heavy get ticket endpoint', () => {
  assert.match(apiClient, /caseDetail:\s*\(ticketId: number\) => request<CaseDetail>\(`\/api\/tickets\/\$\{ticketId\}\/summary`\)/)
  assert.doesNotMatch(workspaceRoute, /api\.ticket\(/)
  assert.doesNotMatch(apiClient, /request<CaseDetail>\(`\/api\/tickets\/\$\{ticketId\}`\)/)
})

test('workspace ticket detail also loads timeline with limit=50', () => {
  assert.match(apiClient, /ticketTimeline:\s*\(ticketId: number, params\?: \{ cursor\?: string \| null; limit\?: number \}\)/)
  assert.match(apiClient, /search\.set\('limit', String\(params\?\.limit \?\? 50\)\)/)
  assert.match(workspaceRoute, /queryKey: \['ticketTimeline', selectedId\]/)
  assert.match(workspaceRoute, /queryFn: \(\) => api\.ticketTimeline\(selectedId as number, \{ limit: 50 \}\)/)
})
