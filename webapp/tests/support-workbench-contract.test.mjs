import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const exists = (path) => existsSync(resolve(root, path))
const workspace = [
  'src/features/operator-workspace/OperatorWorkspacePage.tsx',
  'src/features/operator-workspace/OperatorWorkspaceQueue.tsx',
  'src/features/operator-workspace/OperatorWorkspaceCase.tsx',
  'src/features/operator-workspace/OperatorWorkspaceConversation.tsx',
  'src/features/operator-workspace/OperatorWorkspaceActions.tsx',
  'src/features/operator-workspace/operatorWorkspaceState.ts',
].map(read).join('\n')

const typeOwners = [
  'core',
  'operations',
  'channels',
  'runtime',
  'knowledge',
  'webchat',
]


test('router exposes only the owned canonical route files', () => {
  const actual = readdirSync(resolve(root, 'src/routes')).filter((name) => name.endsWith('.tsx')).sort()
  assert.deepEqual(actual, [
    'account.tsx',
    'administration.tsx',
    'agent-control.tsx',
    'channels.tsx',
    'control-tower.tsx',
    'index.tsx',
    'knowledge.tsx',
    'login.tsx',
    'root.tsx',
    'runtime.tsx',
    'webchat.tsx',
    'workspace.tsx',
  ])
})

test('account and administration remain supporting domains, not parallel workbenches', () => {
  const account = read('src/features/account/AccountPage.tsx')
  const administration = [
    'src/features/administration/AdministrationPage.tsx',
    'src/features/administration/UserGovernance.tsx',
    'src/features/administration/TeamGovernance.tsx',
    'src/features/administration/SecurityAuditPanel.tsx',
  ].map(read).join('\n')

  assert.match(account, /supportApi\.changePassword/)
  assert.match(administration, /supportApi\.rolePolicies/)
  assert.match(administration, /supportApi\.securityAudit/)
  assert.doesNotMatch(account + administration, /operatorWorkspaceApi|supportConversations|querySpeedafWaybills|createSpeedafWorkOrder|confirmSpeedafCancel/)
  assert.doesNotMatch(administration, /ROLE_CAPABILITIES|roleCapabilities|hardcodedCapabilities/)
})

test('workspace is the sole queue, conversation and governed-action surface', () => {
  const api = read('src/lib/operatorWorkspaceApi.ts')
  const supportApi = read('src/lib/supportApi.ts')
  for (const text of ['待处理任务', '客户沟通', '处理进度', '已知信息', '下一步', '接手处理', '确认申请取消']) assert.match(workspace, new RegExp(text))
  for (const name of ['operatorWorkspaceApi.reply', 'webchatAcceptHandoff', 'querySpeedafWaybills', 'createSpeedafWorkOrder', 'previewSpeedafCancel', 'confirmSpeedafCancel']) assert.match(workspace, new RegExp(name.replace('.', '\\.')))
  assert.match(workspace, /mergeLatestWorkspaceThread/)
  assert.match(workspace, /conversationEvents/)
  assert.match(workspace, /resolveSupportConversation/)
  assert.match(supportApi, /\/api\/support\/conversations\/resolve/)
  assert.doesNotMatch(supportApi + workspace, /supportConversationDetail|\/api\/support\/conversations\/detail/)
  assert.match(api, /before_message_id/)
  assert.doesNotMatch(workspace + api, /workspace-v2|thread-v2|thread-page/)
})

test('knowledge is one capability-aware implementation', () => {
  const page = read('src/features/knowledge/KnowledgePage.tsx')
  const route = read('src/routes/knowledge.tsx')
  assert.match(page, /KnowledgePage\(\{ canManage \}/)
  assert.match(route, /LazyKnowledgePage canManage/)
  assert.equal(exists('src/features/knowledge/KnowledgeReadOnlyPage.tsx'), false)
})

test('type exports have one declaration and one domain owner', () => {
  const index = read('src/lib/types.ts')
  for (const owner of typeOwners) assert.match(index, new RegExp(`from './types/${owner}'`))
  const files = readdirSync(resolve(root, 'src/lib/types')).filter((name) => name.endsWith('.ts'))
  const contents = files.map((name) => read(`src/lib/types/${name}`)).join('\n')
  for (const name of ['SupportConversation', 'KnowledgeItem', 'ChannelAccount', 'RuntimeSnapshot']) {
    assert.equal((contents.match(new RegExp(`(?:interface|type) ${name}\\b`, 'g')) || []).length, 1)
  }
})

test('visual inventory is source-converged', () => {
  const routes = readdirSync(resolve(root, 'src/routes')).filter((name) => name.endsWith('.tsx')).map((name) => read(`src/routes/${name}`)).join('\n')
  assert.doesNotMatch(routes, /legacy|deprecated|compatibility/i)
  assert.equal(exists('src/features/operator-console'), false)
  assert.equal(exists('src/features/support-workbench'), false)
})

test('Knowledge and Workspace each have one implementation graph', () => {
  const routes = readdirSync(resolve(root, 'src/routes')).filter((name) => name.endsWith('.tsx')).map((name) => read(`src/routes/${name}`)).join('\n')
  assert.equal((routes.match(/features\/knowledge\/lazy/g) || []).length, 1)
  assert.equal((routes.match(/features\/operator-workspace\/lazy/g) || []).length, 1)
})

test('workspace has one presentation, formatting and action authority', () => {
  const files = readdirSync(resolve(root, 'src/features/operator-workspace')).filter((name) => name.endsWith('.tsx') || name.endsWith('.ts'))
  const contents = files.map((name) => read(`src/features/operator-workspace/${name}`)).join('\n')
  assert.equal((contents.match(/function formatDateTime/g) || []).length, 1)
  assert.equal((contents.match(/function describeError/g) || []).length, 1)
  assert.equal((contents.match(/function actionLabel/g) || []).length, 1)
})
