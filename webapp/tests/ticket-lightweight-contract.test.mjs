import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const workspaceRoute = readFileSync(resolve(root, 'src/routes/workspace.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')

test('ticket detail first paint does not call heavy GET /api/tickets/{id}', () => {
  assert.doesNotMatch(workspaceRoute, /api\.ticket\(/)
  assert.doesNotMatch(apiClient, /request<CaseDetail>\(`\/api\/tickets\/\$\{ticketId\}`\)/)
})

test('ticket detail first paint calls summary plus timeline', () => {
  assert.match(workspaceRoute, /api\.caseDetail\(selectedId as number\)/)
  assert.match(workspaceRoute, /api\.ticketTimeline\(selectedId as number, \{ limit: 50 \}\)/)
  assert.match(apiClient, /`\/api\/tickets\/\$\{ticketId\}\/summary`/)
  assert.match(apiClient, /`\/api\/tickets\/\$\{ticketId\}\/timeline\?\$\{search\.toString\(\)\}`/)
})
