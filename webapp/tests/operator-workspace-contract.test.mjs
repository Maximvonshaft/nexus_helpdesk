import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const page = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
const api = read('src/lib/operatorWorkspaceApi.ts')
const sharedApi = read('src/lib/apiClient.ts')
const presentation = read('src/lib/operatorWorkspacePresentation.ts')
const componentDir = resolve(root, 'src/features/operator-workspace/components')

test('workspace is decomposed into customer-service domains', () => {
  const files = readdirSync(componentDir).filter((name) => name.endsWith('.tsx')).sort()
  assert.deepEqual(files, ['CaseOverview.tsx', 'ConversationPanel.tsx', 'OutcomePanel.tsx', 'QueuePanel.tsx', 'ScopeFiltersPanel.tsx', 'ServiceActionsPanel.tsx'])
  assert.ok(page.split(/\r?\n/).length <= 420)
  for (const file of files) assert.ok(read(`src/features/operator-workspace/components/${file}`).split(/\r?\n/).length <= 420)
})

test('workspace consumes one scoped queue and one shared request client', () => {
  assert.match(api, /\/api\/admin\/operator-queue\/unified/)
  assert.match(api, /X-Nexus-Tenant/)
  assert.match(api, /country_code/)
  assert.match(api, /channel_key/)
  assert.match(api, /apiRequest/)
  assert.doesNotMatch(api, /\bfetch\(|new AbortController/)
  assert.match(sharedApi, /export async function apiRequest/)
})

test('operator flow is customer-service first', () => {
  for (const phrase of ['客服工作台', '客户待办', '客户沟通', '处理动作', '实际结果', '放弃未发送的回复']) {
    assert.match(page + [...readdirSync(componentDir).map((file) => read(`src/features/operator-workspace/components/${file}`))].join('\n'), new RegExp(phrase))
  }
  assert.match(presentation, /历史处理建议/)
  assert.doesNotMatch(presentation, /label:\s*'AI/)
})

test('mobile queue, case, conversation and action surfaces remain reachable', () => {
  for (const view of ['queue', 'case', 'conversation', 'actions']) assert.match(page, new RegExp(`'${view}'`))
  assert.match(page, /workspace-conversation/)
  assert.match(page, /focus\(\{ preventScroll: true \}\)/)
  assert.equal(existsSync(resolve(root, 'e2e/operator-workspace.spec.ts')), true)
})
