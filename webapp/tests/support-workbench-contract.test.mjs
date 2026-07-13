import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const router = read('src/router.tsx')
const workspaceRoute = read('src/routes/workspace.tsx')
const webchatRoute = read('src/routes/webchat.tsx')
const supportConsole = read('src/features/support-console/SupportConsolePage.tsx')
const supportCss = read('src/features/support-console/support-console.css')
const confirmDialog = read('src/components/ui/ConfirmDialog.tsx')
const componentCss = read('src/styles/components.css')
const routeFiles = readdirSync(resolve(root, 'src/routes')).filter((name) => name.endsWith('.tsx')).sort()

test('production router exposes canonical workspace and transitional support workbench routes', () => {
  assert.deepEqual(routeFiles, ['index.tsx', 'login.tsx', 'root.tsx', 'webchat.tsx', 'workspace.tsx'])
  assert.match(router, /LoginRoute/)
  assert.match(router, /IndexRoute/)
  assert.match(router, /WorkspaceRoute/)
  assert.match(router, /WebchatRoute/)
  assert.match(workspaceRoute, /path: '\/workspace'/)
  assert.match(workspaceRoute, /features\/operator-workspace\/lazy/)
  for (const staleRoute of [
    'EmailRoute',
    'WebCallRoute',
    'KnowledgeStudioRoute',
    'RuntimeRoute',
    'ControlTowerRoute',
    'AccountsRoute',
    'UsersRoute',
    'SecurityRoute',
  ]) {
    assert.doesNotMatch(router, new RegExp(staleRoute))
  }
})

test('webchat route mounts the lightweight support console through an async boundary', () => {
  assert.match(webchatRoute, /lazy\(\(\) => import\(['"]@\/features\/support-console\/lazy['"]\)\)/)
  assert.match(webchatRoute, /Suspense/)
  assert.doesNotMatch(webchatRoute, /import \{ SupportConsolePage \} from/)
  assert.doesNotMatch(webchatRoute, /support-console\.css/)
  assert.doesNotMatch(webchatRoute, /AppShell/)
  assert.doesNotMatch(webchatRoute, /WebchatInboxV5Page/)
})

test('support workbench consolidates conversations, knowledge, channels, and runtime', () => {
  for (const view of ['conversations', 'knowledge', 'channels', 'runtime']) {
    assert.match(supportConsole, new RegExp(`'${view}'`))
  }
  assert.match(supportConsole, /supportApi\.supportConversations/)
  assert.match(supportConsole, /supportApi\.supportConversationDetail/)
  assert.match(supportConsole, /supportApi\.supportConversationReply/)
  assert.match(supportConsole, /supportApi\.querySpeedafWaybills/)
  assert.match(supportConsole, /supportApi\.createSpeedafWorkOrder/)
  assert.match(supportConsole, /supportApi\.submitSpeedafAddressUpdate/)
  assert.match(supportConsole, /supportApi\.previewSpeedafCancel/)
  assert.match(supportConsole, /supportApi\.confirmSpeedafCancel/)
  assert.match(supportConsole, /supportApi\.knowledgeStudio/)
  assert.match(supportConsole, /supportApi\.knowledgeItems/)
  assert.match(supportConsole, /supportApi\.createKnowledgeItem/)
  assert.match(supportConsole, /supportApi\.updateKnowledgeItem/)
  assert.match(supportConsole, /supportApi\.publishKnowledgeItem/)
  assert.match(supportConsole, /supportApi\.testKnowledgeRetrieval/)
  assert.match(supportConsole, /supportApi\.channelAccounts/)
  assert.match(supportConsole, /supportApi\.whatsappNativeStatus/)
  assert.match(supportConsole, /supportApi\.providerRuntimeStatus/)
  assert.doesNotMatch(supportConsole, /api\.runtimeHealth/)
  assert.match(supportConsole, /supportApi\.supportConversationMetrics/)
})

test('support workbench keeps conversation search and message scrolling responsive', () => {
  assert.match(supportConsole, /useDeferredValue/)
  assert.match(supportConsole, /deferredQuery/)
  assert.match(supportConsole, /queryKey: \['supportConversations', view, channel, deferredQuery\]/)
  assert.match(supportConsole, /enabled: activeView === 'conversations'/)
  assert.match(supportConsole, /refetchInterval: activeView === 'conversations' \? 5000 : false/)
  assert.match(supportConsole, /refetchInterval: activeView === 'conversations' \? 4000 : false/)
  assert.match(supportConsole, /staleTime: 1000/)
  assert.match(supportConsole, /readSupportWorkbenchSearch/)
  assert.match(supportConsole, /window\.history\.replaceState/)
  assert.match(supportConsole, /params\.set\('session', selectedSessionKey\)/)
  assert.match(supportConsole, /beforeunload/)
  assert.match(supportConsole, /messagesRef/)
  assert.match(supportConsole, /node\.scrollTop = node\.scrollHeight/)
  assert.match(supportConsole, /compactLatency/)
  assert.match(supportConsole, /aiReplySourceLabel/)
  assert.match(supportConsole, /统一 AI Runtime/)
  assert.match(supportConsole, /last_bridge_elapsed_ms/)
  assert.match(supportConsole, /last_ai_reply_source/)
  assert.match(supportConsole, /ai_status_elapsed_ms/)
  assert.match(supportConsole, /runtime_trace/)
  assert.match(supportConsole, /support-runtime-trace/)
})

test('runtime view uses provider runtime diagnostics instead of legacy external channel health', () => {
  assert.match(supportConsole, /supportWorkbenchProviderRuntimeStatus/)
  assert.match(supportConsole, /private_ai_runtime/)
  assert.match(supportConsole, /runtimeDiagnostics\.direct_model/)
  assert.match(supportConsole, /runtimeDiagnostics\.rag_model/)
  assert.match(supportConsole, /runtimeDiagnostics\.rag_runtime_isolated/)
  assert.match(supportConsole, /runtimeDiagnostics\.allow_shared_rag_model/)
  assert.doesNotMatch(supportConsole, /pending_sync_jobs/)
  assert.doesNotMatch(supportConsole, /external_dead_outbound/)
})

test('support workbench does not reintroduce customer-visible template reply resources', () => {
  assert.doesNotMatch(supportConsole, /suggestedReply/)
  assert.doesNotMatch(supportConsole, /defaultReply/)
  assert.doesNotMatch(supportConsole, /quickReply/)
  assert.doesNotMatch(supportConsole, /canned/i)
})

test('support workbench only presents active production channel accounts', () => {
  assert.match(supportConsole, /activeAccounts/)
  assert.match(supportConsole, /filter\(\(item: ChannelAccount\) => item\.is_active\)/)
  assert.doesNotMatch(supportConsole, /\(accounts\.data \?\? \[\]\)\.find\(\(item: ChannelAccount\) => item\.provider === 'whatsapp'\)/)
})

test('support workbench exposes controlled Speedaf actions without customer templates', () => {
  assert.match(supportConsole, /SpeedafControlledActionsPanel/)
  assert.match(supportConsole, /activeConversation\?\.tracking_number/)
  assert.match(supportConsole, /contactPhoneCandidate\(activeConversation\?\.customer_contact\)/)
  assert.match(supportConsole, /key=\{activeConversation\?\.session_key \|\| 'no-conversation'\}/)
  assert.match(supportConsole, /clearActionResults/)
  assert.match(supportConsole, /电话查单/)
  assert.match(supportConsole, /查询运单/)
  assert.match(supportConsole, /催派工单/)
  assert.match(supportConsole, /联系号码更新/)
  assert.match(supportConsole, /取消预检/)
  assert.match(supportConsole, /workOrderType: 'WT0103-05'/)
  assert.match(supportConsole, /confirmToken/)
  assert.doesNotMatch(supportConsole, /Please provide your tracking number/)
  assert.doesNotMatch(supportConsole, /so I can check/i)
})

test('knowledge workbench is operator-maintainable instead of a read-only dashboard', () => {
  assert.match(supportConsole, /知识库维护/)
  assert.match(supportConsole, /新建知识/)
  assert.match(supportConsole, /编辑知识/)
  assert.match(supportConsole, /客户会怎么问/)
  assert.match(supportConsole, /AI 应该知道的答案/)
  assert.match(supportConsole, /同义问法/)
  assert.match(supportConsole, /客户可见范围/)
  assert.match(supportConsole, /知识分类筛选/)
  assert.match(supportConsole, /处理流程/)
  assert.match(supportConsole, /优先级数字越小越靠前/)
  assert.match(supportConsole, /身份、人设和语言风格属于助手设定/)
  assert.match(supportConsole, /保存草稿/)
  assert.match(supportConsole, /审核并发布/)
  assert.doesNotMatch(supportConsole, />上线当前草稿</)
  assert.doesNotMatch(supportConsole, />保存并上线</)
  assert.match(supportConsole, /测试知识命中/)
  assert.match(supportConsole, /让 AI 组织语言/)
  assert.match(supportConsole, /不写固定话术/)
  assert.match(supportConsole, /supportApi\.knowledgeItems/)
  assert.match(supportConsole, /supportApi\.createKnowledgeItem/)
  assert.match(supportConsole, /supportApi\.updateKnowledgeItem/)
  assert.match(supportConsole, /supportApi\.publishKnowledgeItem/)
  assert.match(supportConsole, /supportApi\.testKnowledgeRetrieval/)
  assert.doesNotMatch(supportConsole, /知识库接口未返回发布生命周期/)
})

test('support workbench keeps stable desktop and mobile zones', () => {
  assert.match(supportCss, /grid-template-columns: minmax\(290px, 360px\) minmax\(0, 1fr\) minmax\(280px, 340px\)/)
  assert.match(supportCss, /\.support-context/)
  assert.match(supportCss, /\.support-action-grid/)
  assert.match(supportCss, /\.support-knowledge-workbench/)
  assert.match(supportCss, /\.support-knowledge-grid/)
  assert.match(supportCss, /@media \(max-width: 980px\)/)
  assert.match(supportCss, /@media \(max-width: 640px\)/)
})



test('knowledge drafts are guarded and publication crosses an explicit review boundary', () => {
  assert.match(supportConsole, /serializeKnowledgeDraft/)
  assert.match(supportConsole, /knowledgeDirty/)
  assert.match(supportConsole, /beforeunload/)
  assert.match(supportConsole, /放弃未保存的修改/)
  assert.match(supportConsole, /审核并发布知识/)
  assert.match(supportConsole, /发布后，AI Runtime 只能在后续同步完成后使用/)
  assert.match(supportConsole, /role="status"/)
  assert.match(supportConsole, /aria-live="polite"/)
  assert.match(confirmDialog, /@radix-ui\/react-dialog/)
  assert.match(confirmDialog, /Dialog\.Title/)
  assert.match(confirmDialog, /Dialog\.Description/)
  assert.match(componentCss, /\.nd-dialog__overlay/)
  assert.match(componentCss, /\.nd-dialog__content/)
})
