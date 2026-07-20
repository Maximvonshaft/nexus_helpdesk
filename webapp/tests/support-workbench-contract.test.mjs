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
  assert.match(page, /KnowledgePage\(\{ canManage \}\/)/
  assert.match(route, /LazyKnowledgePage canManage/)
  assert.equal(exists('src/features/knowledge/KnowledgeReadOnlyPage.tsx'), false)
  for (const text of ['知识与流程', '标准答案与处理步骤', '搜索测试', '发布状态']) assert.match(page, new RegExp(text))
})

test('type exports have one declaration and one domain owner', () => {
  const barrel = read('src/lib/types.ts')
  const declarations = new Map()

  for (const owner of typeOwners) {
    const path = `src/lib/types/${owner}.ts`
    const source = read(path)
    assert.match(barrel, new RegExp(`export \\* from './types/${owner}'`))
    for (const match of source.matchAll(/export\s+(?:interface|type)\s+([A-Za-z_$][\w$]*)/g)) {
      const name = match[1]
      assert.equal(declarations.has(name), false, `${name} is declared by both ${declarations.get(name)} and ${owner}`)
      declarations.set(name, owner)
    }
    assert.doesNotMatch(source, /export\s*\{[^}]*\bas\b/, `${owner} contains a compatibility alias`)
  }

  const channelControlSource = read('src/lib/channelControlTypes.ts')
  assert.match(barrel, /export \* from '.\/channelControlTypes'/)
  for (const match of channelControlSource.matchAll(/export\s+(?:interface|type)\s+([A-Za-z_$][\w$]*)/g)) {
    const name = match[1]
    assert.equal(declarations.has(name), false, `${name} is declared by both ${declarations.get(name)} and channelControlTypes`)
    declarations.set(name, 'channelControlTypes')
  }
  assert.doesNotMatch(channelControlSource, /export\s*\{[^}]*\bas\b/, 'channelControlTypes contains a compatibility alias')

  const operations = read('src/lib/types/operations.ts')
  const knowledge = read('src/lib/types/knowledge.ts')
  const channelControl = channelControlSource
  assert.doesNotMatch(operations, /KnowledgeStudio|PersonaBuilder|ChannelOnboardingTask/)
  assert.match(knowledge, /KnowledgeStudio/)
  assert.match(knowledge, /PersonaBuilder/)
  assert.doesNotMatch(knowledge, /ChannelOnboardingTask/)
  assert.match(channelControl, /ChannelOnboardingTask/)
  assert.doesNotMatch(channelControl, /externalAccountId/)
})
