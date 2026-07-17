import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
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
].map(read).join('\n')

test('one MUI theme and one bounded operator presentation own visual semantics', () => {
  const main = read('src/main.tsx')
  const theme = read('src/theme/nexusTheme.ts')
  const provider = read('src/theme/NexusThemeProvider.tsx')
  const presentation = read('src/app/OperatorPresentation.tsx')
  assert.match(main, /NexusThemeProvider/)
  assert.match(provider, /ThemeProvider/)
  assert.match(provider, /CssBaseline/)
  assert.match(theme, /createTheme/)
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
  ]) assert.match(presentation, new RegExp(name))
  assert.doesNotMatch(presentation, /export function (Button|Input|Dialog|Field)/)
})

test('retired custom and duplicate visual authorities are absent', () => {
  for (const path of [
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
  ]) assert.equal(exists(path), false, path)
})

test('Login, shell and Workspace use MUI and concise operator language', () => {
  const login = read('src/routes/login.tsx')
  const shell = read('src/app/AppShell.tsx')
  assert.match(login, /@mui\/material/)
  assert.match(login, /账号或密码错误/)
  assert.match(shell, /AppBar/)
  assert.doesNotMatch(shell, /component=["']main["']/)
  assert.match(workspace, /ListItemButton/)
  assert.match(workspace, /Tabs/)
  assert.match(workspace, /Dialog/)
  assert.match(workspace, /处理进度/)
  assert.match(workspace, /待处理任务/)
  assert.doesNotMatch(login + shell + workspace, /客服与运营工作台|案例处理链路|事实与证据|服务端最终授权|nd-button|nd-field|nd-badge/)
})

test('canonical route pages own their single main landmark', () => {
  for (const path of [
    'src/features/operator-workspace/OperatorWorkspacePage.tsx',
    'src/features/knowledge/KnowledgePage.tsx',
    'src/features/channels/ChannelsPage.tsx',
    'src/features/runtime/RuntimePage.tsx',
    'src/features/control-tower/ControlTowerPage.tsx',
  ]) assert.match(read(path), /component=["']main["']/, path)
})

test('global CSS is bounded and browser evidence exists', () => {
  const styles = read('src/styles.css')
  const a11y = read('src/a11y.css')
  assert.doesNotMatch(styles, /--nd-|\.Mui/)
  assert.match(a11y, /^\.sr-only/)
  assert.doesNotMatch(a11y, /--nd-|\.nd-|\.auth-|\.operator-/)
  assert.equal(exists('e2e/login-semantic.spec.ts'), true)
})
