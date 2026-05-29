import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const route = readFileSync(resolve(root, 'src/routes/qa-training.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const access = readFileSync(resolve(root, 'src/lib/access.ts'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const types = readFileSync(resolve(root, 'src/lib/types.ts'), 'utf8')

test('qa training route is registered and capability gated', () => {
  assert.match(route, /path: '\/qa-training'/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/qa-training'\]\}>/)
  assert.match(router, /QATrainingRoute/)
  assert.match(router, /@\/routes\/qa-training/)
  assert.match(rbac, /qaRead: 'qa\.read'/)
  assert.match(rbac, /qaManage: 'qa\.manage'/)
  assert.match(rbac, /'\/qa-training': \{ anyOf: \[CAPABILITIES\.qaRead, CAPABILITIES\.qaManage\] \}/)
  assert.match(rbac, /createQAReview: \{ allOf: \[CAPABILITIES\.qaManage\] \}/)
  assert.match(access, /function canReadQA/)
  assert.match(access, /function canManageQA/)
})

test('qa training workbench is reachable from shell and command palette', () => {
  assert.match(appShell, /to: '\/qa-training'[\s\S]*label: 'QA \/ Training'[\s\S]*access: routeAccess\['\/qa-training'\]/)
  assert.match(appShell, /\{ label: '治理与运维', items: \['\/runtime', '\/ai-control', '\/qa-training'/)
  assert.match(commandPalette, /id: 'qa-training'[\s\S]*to: '\/qa-training'[\s\S]*access: routeAccess\['\/qa-training'\]/)
})

test('qa training frontend uses unified api client and no raw fetch', () => {
  assert.match(apiClient, /qaTrainingQueue: \(params\?: \{ channel\?: string; status\?: string; limit\?: number \}\)/)
  assert.match(apiClient, /\/api\/admin\/qa-training\/queue/)
  assert.match(apiClient, /qaTrainingTasks: \(params\?: \{ status\?: string; limit\?: number \}\)/)
  assert.match(apiClient, /\/api\/admin\/qa-training\/training-tasks/)
  assert.match(apiClient, /createQAReview: \(payload: QAReviewPayload\)/)
  assert.match(apiClient, /\/api\/admin\/qa-training\/reviews/)
  assert.match(route, /api\.qaTrainingQueue/)
  assert.match(route, /api\.qaTrainingTasks/)
  assert.match(route, /api\.createQAReview/)
  assert.doesNotMatch(route, /\bfetch\s*\(/)
})

test('qa training page closes scorecard, coaching, gap, timeline and audit semantics', () => {
  assert.match(types, /interface QAQueueSample/)
  assert.match(types, /interface QAReviewPayload/)
  assert.match(types, /interface QATrainingTask/)
  assert.match(route, /data-testid="qa-training-queue"/)
  assert.match(route, /data-testid="qa-scorecard"/)
  assert.match(route, /data-testid="qa-training-tasks"/)
  assert.match(route, /客户问题/)
  assert.match(route, /标记知识缺口/)
  assert.match(route, /AI Ops 审核/)
  assert.match(route, /黄金测试/)
  assert.match(route, /命中监控/)
  assert.match(route, /timeline \/ audit/)
  assert.match(route, /invalidateQueries\(\{ queryKey: \['ticketTimeline', review\.ticket_id\] \}\)/)
  assert.match(route, /缺少 qa\.manage/)
})
