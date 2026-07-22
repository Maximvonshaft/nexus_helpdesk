import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const webappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(webappRoot, '..')
const read = (relative) => fs.readFileSync(path.join(webappRoot, relative), 'utf8')
const workspace = [
  'src/features/operator-workspace/OperatorWorkspacePage.tsx',
  'src/features/operator-workspace/OperatorWorkspaceQueue.tsx',
  'src/features/operator-workspace/OperatorWorkspaceCase.tsx',
  'src/features/operator-workspace/OperatorWorkspaceConversation.tsx',
  'src/features/operator-workspace/OperatorWorkspaceActions.tsx',
  'src/features/operator-workspace/operatorWorkspaceState.ts',
].map(read).join('\n')

test('retired frontend and visual authorities are physically absent', () => {
  for (const relative of [
    'src/features/support-console',
    'src/shared/ui',
    'src/shared/api',
    'src/lib/api.ts',
    'src/components/ui',
    'src/styles/tokens.css',
    'src/styles/components.css',
    'src/styles/auth.css',
    'src/app/app-shell.css',
    'src/features/operator-workspace/OperatorWorkspaceCommon.tsx',
    'src/features/operator-workspace/operator-workspace.css',
    'src/features/operator-workspace/operator-workspace-refinements.css',
    'src/features/admin-routes/admin-routes.css',
    'src/features/knowledge/knowledge.css',
    'src/features/knowledge/KnowledgeReadOnlyPage.tsx',
    'src/features/runtime/runtime-evidence-audit.css',
  ]) assert.equal(fs.existsSync(path.join(webappRoot, relative)), false, relative)
  assert.equal(fs.existsSync(path.join(repositoryRoot, 'frontend')), false)
})

test('workspace has one shell, one bounded thread state and input-bound cancel preview', () => {
  const page = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
  const actions = read('src/features/operator-workspace/OperatorWorkspaceActions.tsx')
  const state = read('src/features/operator-workspace/operatorWorkspaceState.ts')
  const api = read('src/lib/operatorWorkspaceApi.ts')
  assert.doesNotMatch(workspace, /function\s+AppNavigation\b|operator-app-header|\/webchat\?tab=/)
  assert.match(actions, /type CancelPreviewBinding/)
  assert.match(state, /ticketId[\s\S]*waybill[\s\S]*caller[\s\S]*reasonCode/)
  assert.match(actions, /cancelPreview\.fingerprint !== currentCancelFingerprint/)
  assert.match(workspace, /处理进度/)
  assert.match(state, /mergeLatestWorkspaceThread/)
  assert.match(state, /mergeOlderWorkspaceThread/)
  assert.match(page, /conversationEvents/)
  assert.match(workspace, /加载更早消息/)
  assert.match(api, /before_message_id/)
  assert.doesNotMatch(api + workspace, /thread-v2|thread-page|workspace-v2/)
  assert.doesNotMatch(workspace, /案例处理链路|服务端最终授权|事实与证据/)
})

test('MUI, operator presentation and operational status are the only shared authorities', () => {
  const theme = read('src/theme/nexusTheme.ts')
  const provider = read('src/theme/NexusThemeProvider.tsx')
  const presentation = read('src/app/OperatorPresentation.tsx')
  const status = read('src/domain/operationalPresentation.ts')
  const supportStatus = read('src/lib/supportStatus.ts')
  const workspaceStatus = read('src/lib/operatorWorkspacePresentation.ts')
  assert.match(theme, /createTheme\(/)
  assert.match(provider, /<ThemeProvider theme=\{nexusTheme\}>/)
  assert.match(provider, /<CssBaseline \/>/)
  for (const name of [
    'OperatorPageBoundary',
    'OperatorEmptyState',
    'OperatorErrorNotice',
    'OperatorLoadingState',
    'RouteLoadingState',
    'OperatorFactGrid',
    'OperatorSectionHeading',
    'OperatorStatusLine',
    'OperatorTechnicalDisclosure',
  ]) assert.match(presentation, new RegExp(`export function ${name}`))
  assert.match(status, /technical_complete/)
  assert.match(status, /operational_complete/)
  assert.match(status, /customer_notified/)
  assert.match(supportStatus, /operationalPresentation\(status, message\)/)
  assert.match(supportStatus, /export function channelPresentation/)
  assert.match(workspaceStatus, /return operationalPresentation\(statusValue, messageValue\)/)
  assert.match(workspaceStatus, /自动回复建议/)
  assert.doesNotMatch(workspaceStatus, /className\??:|className\s*:/)
})

test('runtime and knowledge permissions use one route projection each', () => {
  const runtimeRoute = read('src/routes/runtime.tsx')
  const knowledgeRoute = read('src/routes/knowledge.tsx')
  const knowledge = read('src/features/knowledge/KnowledgePage.tsx')
  assert.match(runtimeRoute, /runtime\.manage/)
  assert.match(runtimeRoute, /audit\.read/)
  assert.match(knowledgeRoute, /canManage/)
  assert.match(knowledge, /KnowledgePage\(\{ canManage \}/)
  assert.doesNotMatch(knowledgeRoute + knowledge, /KnowledgeReadOnlyPage/)
})

test('control tower accepts only canonical hrefs in the browser', () => {
  const source = read('src/features/control-tower/ControlTowerPage.tsx')
  assert.match(source, /canonicalAppHref/)
  assert.match(source, /OperatorStatusLine/)
  assert.doesNotMatch(source, /function\s+StatusCount|const\s+toneColor/)
  assert.doesNotMatch(source, /\/accounts|\/outbound-email|\/ai-control/)
})

test('dependency graph selects MUI and excludes retired or parallel visual packages', () => {
  const manifest = JSON.parse(read('package.json'))
  assert.equal(manifest.dependencies?.['@mui/material'], '9.2.0')
  assert.equal(manifest.dependencies?.['@mui/icons-material'], '9.2.0')
  assert.equal(manifest.dependencies?.['@emotion/react'], '11.14.0')
  assert.equal(manifest.dependencies?.['@emotion/styled'], '11.14.1')
  assert.equal(manifest.dependencies?.['react-is'], '18.3.1')
  assert.equal(manifest.overrides?.['react-is'], '18.3.1')
  assert.equal(manifest.dependencies?.['livekit-client'], '2.20.2')
  for (const dependency of ['@radix-ui/react-dialog', '@chakra-ui/react', '@mantine/core', 'antd', 'tailwindcss', 'bootstrap', 'clsx']) {
    assert.equal(manifest.dependencies?.[dependency], undefined, dependency)
  }
})
