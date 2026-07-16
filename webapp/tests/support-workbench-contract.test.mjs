import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const exists = (path) => existsSync(resolve(root, path))

const router = read('src/router.tsx')
const workspace = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
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
  for (const routeName of [
    'LoginRoute',
    'IndexRoute',
    'WorkspaceRoute',
    'KnowledgeRoute',
    'ChannelsRoute',
    'RuntimeRoute',
    'ControlTowerRoute',
    'WebchatRoute',
  ]) {
    assert.match(router, new RegExp(routeName))
  }
})

test('workspace is the only conversation queue, reply, handoff and governed-action product surface', () => {
  assert.match(workspace, /待处理任务/)
  assert.match(workspace, /客户沟通/)
  assert.match(workspace, /operatorWorkspaceApi\.reply/)
  assert.match(workspace, /webchatAcceptHandoff/)
  assert.match(workspace, /webchatForceTakeover/)
  assert.match(workspace, /webchatReleaseHandoff/)
  assert.match(workspace, /webchatResumeAi/)
  assert.match(workspace, /querySpeedafWaybills/)
  assert.match(workspace, /createSpeedafWorkOrder/)
  assert.match(workspace, /submitSpeedafAddressUpdate/)
  assert.match(workspace, /previewSpeedafCancel/)
  assert.match(workspace, /confirmSpeedafCancel/)
  assert.match(workspace, /案例处理链路/)
})

test('knowledge is a complete maintainable MUI route with draft, review, publication and retrieval evidence', () => {
  assert.match(knowledge, /知识与流程/)
  assert.match(knowledge, /客户会怎么问/)
  assert.match(knowledge, /答案事实与处理规则/)
  assert.match(knowledge, /保存草稿/)
  assert.match(knowledge, /审核并发布/)
  assert.match(knowledge, /测试知识命中/)
  assert.match(knowledge, /supportApi\.knowledgeItems/)
  assert.match(knowledge, /supportApi\.createKnowledgeItem/)
  assert.match(knowledge, /supportApi\.updateKnowledgeItem/)
  assert.match(knowledge, /supportApi\.publishKnowledgeItem/)
  assert.match(knowledge, /supportApi\.testKnowledgeRetrieval/)
  assert.match(knowledge, /beforeunload/)
  assert.match(knowledge, /放弃未保存的修改/)
  assert.match(knowledge, /<Dialog/)
  assert.equal(exists('src/components/ui/ConfirmDialog.tsx'), false)
  assert.equal(exists('src/styles/components.css'), false)
})

test('channels and runtime are separate bounded MUI administrative domains', () => {
  assert.match(channels, /渠道管理/)
  assert.match(channels, /supportApi\.channelAccounts/)
  assert.match(channels, /supportApi\.whatsappNativeStatus/)
  assert.match(channels, /maskPhone/)
  assert.match(channels, /<Table/)
  assert.match(runtime, /系统运行/)
  assert.match(runtime, /supportApi\.providerRuntimeStatus/)
  assert.match(runtime, /supportApi\.supportConversationMetrics/)
  assert.match(runtime, /<Accordion/)
  assert.doesNotMatch(runtime, /模型名称/)
})

test('control tower is a management projection that drills into canonical routes', () => {
  assert.match(controlTower, /supportApi\.controlTower/)
  assert.match(controlTower, /canonicalAppHref/)
  assert.match(controlTower, /<Table/)
  assert.match(canonicalRoutes, /\/workspace/)
  assert.match(canonicalRoutes, /\/channels/)
  assert.match(canonicalRoutes, /\/runtime/)
  assert.match(canonicalRoutes, /\/knowledge/)
  assert.doesNotMatch(controlTower, /second queue/i)
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

test('webchat remains compatibility-only and the competing support console cannot return', () => {
  assert.match(webchatRoute, /WebchatCompatibilityRedirect/)
  assert.match(webchatRoute, /旧客服后台入口已合并到统一操作员后台/)
  assert.match(webchatRoute, /workspace\?session=/)
  assert.doesNotMatch(webchatRoute, /supportConversationDetail/)
  assert.doesNotMatch(webchatRoute, /support-console/)
  assert.equal(exists('src/features/support-console/SupportConsolePage.tsx'), false)
  assert.equal(exists('src/features/support-console/lazy.tsx'), false)
  assert.equal(exists('src/features/support-console/support-console.css'), false)
})
