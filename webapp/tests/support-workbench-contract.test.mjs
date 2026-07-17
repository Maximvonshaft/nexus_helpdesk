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
  'src/features/operator-workspace/operatorWorkspaceState.ts',
].map(read).join('\n')

const typeOwners = [
  'core',
  'operations',
  'channels',
  'channelControl',
  'runtime',
  'knowledge',
  'webchat',
]


test('router exposes only the owned canonical route files', () => {
  const actual = readdirSync(resolve(root, 'src/routes')).filter((name) => name.endsWith('.tsx')).sort()
  assert.deepEqual(actual, ['channels.tsx', 'control-tower.tsx', 'index.tsx', 'knowledge.tsx', 'login.tsx', 'root.tsx', 'runtime.tsx', 'webchat.tsx', 'workspace.tsx'])
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

  const operations = read('src/lib/types/operations.ts')
  const knowledge = read('src/lib/types/knowledge.ts')
  const channelControl = read('src/lib/types/channelControl.ts')
  assert.doesNotMatch(operations, /KnowledgeStudio|PersonaBuilder|ChannelOnboardingTask|ExternalChannelUnresolvedEvent/)
  assert.match(knowledge, /KnowledgeStudio/)
  assert.match(knowledge, /PersonaBuilder/)
  assert.doesNotMatch(knowledge, /ChannelOnboardingTask|ExternalChannelUnresolvedEvent/)
  assert.match(channelControl, /ChannelOnboardingTask/)
  assert.match(channelControl, /ExternalChannelUnresolvedEvent/)
})

test('MUI and one bounded presentation module are the only generic visual authorities', () => {
  const theme = read('src/theme/nexusTheme.ts')
  const presentation = read('src/app/OperatorPresentation.tsx')
  assert.match(theme, /createTheme/)
  assert.match(presentation, /OperatorEmptyState/)
  assert.match(presentation, /OperatorErrorNotice/)
  assert.equal(exists('src/components/ui'), false)
  for (const path of ['src/styles/tokens.css', 'src/styles/components.css', 'src/styles/auth.css', 'src/app/app-shell.css', 'src/features/operator-workspace/operator-workspace.css', 'src/features/knowledge/knowledge.css']) assert.equal(exists(path), false, path)
})

test('webchat remains a concise compatibility redirect only', () => {
  const source = read('src/routes/webchat.tsx')
  assert.match(source, /WebchatCompatibilityRedirect/)
  assert.match(source, /正在跳转/)
  assert.match(source, /workspace\?session=/)
  assert.doesNotMatch(source, /supportConversationDetail|support-console|旧客服后台入口已合并/)
})
