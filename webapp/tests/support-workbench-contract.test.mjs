import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const exists = (path) => existsSync(resolve(root, path))

const router = read('src/router.tsx')
const workspace = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
const workspaceApi = read('src/lib/operatorWorkspaceApi.ts')
const knowledge = read('src/features/knowledge/KnowledgePage.tsx')
const channels = read('src/features/channels/ChannelsPage.tsx')
const runtime = read('src/features/runtime/RuntimePage.tsx')
const controlTower = read('src/features/control-tower/ControlTowerPage.tsx')
const canonicalRoutes = read('src/app/canonicalRoutes.ts')
const webchatRoute = read('src/routes/webchat.tsx')
const theme = read('src/theme/nexusTheme.ts')
const routeFiles = readdirSync(resolve(root, 'src/routes')).filter((name) => name.endsWith('.tsx')).sort()

test('production router exposes one canonical domain route per supported backend job', () => {
  assert.deepEqual(routeFiles, [
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
  for (const routeName of ['LoginRoute', 'IndexRoute', 'WorkspaceRoute', 'KnowledgeRoute', 'ChannelsRoute', 'RuntimeRoute', 'ControlTowerRoute', 'WebchatRoute']) {
    assert.match(router, new RegExp(routeName))
  }
})

test('workspace is the sole case queue, conversation and governed-action surface', () => {
  for (const label of ['待处理任务', '客户沟通', '处理进度', '已知信息', '下一步']) assert.match(workspace, new RegExp(label))
  for (const authority of [
    'operatorWorkspaceApi.reply',
    'webchatAcceptHandoff',
    'webchatForceTakeover',
    'webchatReleaseHandoff',
    'webchatResumeAi',
    'querySpeedafWaybills',
    'createSpeedafWorkOrder',
    'submitSpeedafAddressUpdate',
    'previewSpeedafCancel',
    'confirmSpeedafCancel',
  ]) assert.match(workspace, new RegExp(authority.replace('.', '\\.')))
  assert.match(workspace, /mergeLatestThread/)
  assert.match(workspace, /mergeOlderThread/)
  assert.match(workspace, /conversationEvents/)
  assert.match(workspaceApi, /before_message_id/)
  assert.doesNotMatch(workspaceApi, /thread-v2|thread-page/)
  assert.doesNotMatch(workspace, /案例处理链路|服务端最终授权|事实与证据/)
})

test('knowledge is complete, maintainable and concise', () => {
  for (const label of ['知识与流程', '客户问题', '标准答案与处理步骤', '保存草稿', '搜索测试', '测试搜索', '发布状态']) {
    assert.match(knowledge, new RegExp(label))
  }
  assert.match(knowledge, />发布</)
  assert.doesNotMatch(knowledge, /客户会怎么问|答案事实与处理规则|审核并发布|测试知识命中|知识同步/)
  for (const authority of ['knowledgeItems', 'createKnowledgeItem', 'updateKnowledgeItem', 'publishKnowledgeItem', 'testKnowledgeRetrieval']) {
    assert.match(knowledge, new RegExp(`supportApi\\.${authority}`))
  }
  assert.match(knowledge, /beforeunload/)
  assert.match(knowledge, /放弃未保存的修改/)
  assert.match(knowledge, /<Dialog/)
})

test('channels and runtime are separate bounded MUI administrative domains', () => {
  assert.match(channels, /渠道管理/)
  assert.match(channels, /supportApi\.channelAccounts/)
  assert.match(channels, /supportApi\.whatsappNativeStatus/)
  assert.match(channels, /<Table/)
  for (const label of ['账号名称', '接入位置', '绑定账号或号码', '系统信息']) assert.match(channels, new RegExp(label))
  assert.doesNotMatch(channels, /目标槽位|期望绑定|创建任务不等于账号已经接通/)

  assert.match(runtime, /系统运行/)
  assert.match(runtime, />系统状态</)
  assert.match(runtime, /服务提供方/)
  assert.match(runtime, /supportApi\.providerRuntimeStatus/)
  assert.match(runtime, /supportApi\.supportConversationMetrics/)
  assert.match(runtime, /<Accordion/)
  assert.doesNotMatch(runtime, /服务就绪状态|降级路径|Provider 诊断|模型名称/)
})

test('control tower is a concise management projection with canonical drill-down', () => {
  assert.match(controlTower, /supportApi\.controlTower/)
  assert.match(controlTower, /canonicalAppHref/)
  assert.match(controlTower, /<Table/)
  assert.match(controlTower, /系统与配置问题/)
  assert.match(controlTower, /去处理/)
  assert.doesNotMatch(controlTower, /运行与治理风险|打开处理页面|后端未返回受支持的处理入口/)
  for (const route of ['/workspace', '/channels', '/runtime', '/knowledge']) assert.match(canonicalRoutes, new RegExp(route.replace('/', '\\/')))
})

test('MUI is the only generic visual authority', () => {
  assert.match(theme, /createTheme\(/)
  assert.match(theme, /MuiButton:/)
  assert.match(theme, /MuiDialog:/)
  assert.equal(exists('src/components/ui'), false)
  for (const path of [
    'src/styles/tokens.css',
    'src/styles/components.css',
    'src/styles/auth.css',
    'src/app/app-shell.css',
    'src/features/operator-workspace/operator-workspace.css',
    'src/features/admin-routes/admin-routes.css',
    'src/features/knowledge/knowledge.css',
    'src/features/runtime/runtime-evidence-audit.css',
  ]) assert.equal(exists(path), false, `retired visual path returned: ${path}`)
})

test('webchat remains compatibility-only with one concise redirect state', () => {
  assert.match(webchatRoute, /WebchatCompatibilityRedirect/)
  assert.match(webchatRoute, /正在跳转…/)
  assert.match(webchatRoute, /workspace\?session=/)
  assert.match(webchatRoute, /from '@mui\/material'/)
  assert.doesNotMatch(webchatRoute, /旧客服后台入口已合并到统一操作员后台|supportConversationDetail|support-console/)
  assert.equal(exists('src/features/support-console/SupportConsolePage.tsx'), false)
  assert.equal(exists('src/features/support-console/lazy.tsx'), false)
  assert.equal(exists('src/features/support-console/support-console.css'), false)
})
