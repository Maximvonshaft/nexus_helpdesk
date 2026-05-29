import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const repoRoot = resolve(root, '..')
const overviewRoute = readFileSync(resolve(root, 'src/routes/index.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')
const backendRoute = readFileSync(resolve(repoRoot, 'backend/app/api/workbench.py'), 'utf8')
const backendMain = readFileSync(resolve(repoRoot, 'backend/app/main.py'), 'utf8')
const backendSchema = readFileSync(resolve(repoRoot, 'backend/app/workbench_schemas.py'), 'utf8')
const initDevDb = readFileSync(resolve(repoRoot, 'backend/scripts/init_dev_db.py'), 'utf8')
const card = readFileSync(resolve(root, 'src/components/ui/Card.tsx'), 'utf8')

test('today workbench uses the unified summary api instead of fixture case feed', () => {
  assert.match(apiClient, /workbenchSummary: \(params\?: \{ limit\?: number \}\)/)
  assert.match(apiClient, /\/api\/workbench\/summary/)
  assert.match(overviewRoute, /api\.workbenchSummary\(\{ limit: 12 \}\)/)
  assert.match(overviewRoute, /queryKey: \['workbenchSummary'\]/)
  assert.doesNotMatch(overviewRoute, /overviewCases/)
  assert.doesNotMatch(overviewRoute, /caseFeed/)
})

test('today workbench renders template blocks from typed backend DTOs', () => {
  for (const typeName of [
    'WorkbenchSummary',
    'WorkbenchMetric',
    'WorkbenchTask',
    'WorkbenchQueueItem',
    'WorkbenchInteractionState',
  ]) {
    assert.match(types, new RegExp(`export interface ${typeName}`))
  }
  for (const testId of [
    'today-workbench-metrics',
    'today-workbench-role-tasks',
    'today-workbench-sla-queue',
    'today-workbench-interaction-states',
    'overview-priority-actions',
  ]) {
    assert.match(overviewRoute, new RegExp(`data-testid="${testId}"`))
  }
  assert.match(card, /HTMLAttributes<HTMLElement>/)
  assert.match(card, /\{\.\.\.props\}/)
})

test('workbench backend route is capability gated and aggregates real channel tables', () => {
  assert.match(backendMain, /from \.api\.workbench import router as workbench_router/)
  assert.match(backendMain, /app\.include_router\(workbench_router\)/)
  assert.match(backendRoute, /ensure_capability\(current_user, CAP_TICKET_READ, db, message="workbench_summary_requires_ticket_read"\)/)
  for (const contract of [
    'TicketOutboundMessage',
    'WebchatHandoffRequest',
    'WebchatConversation',
    'WebchatVoiceSession',
    'CAP_WEBCALL_VOICE_QUEUE_VIEW',
    'CAP_OUTBOUND_DRAFT_SAVE',
    'CAP_OUTBOUND_SEND',
  ]) {
    assert.match(backendRoute, new RegExp(contract))
  }
  assert.match(backendRoute, /ensure_utc/)
  assert.match(backendSchema, /class WorkbenchSummaryRead/)
  assert.match(initDevDb, /voice_models as _voice_models/)
})
