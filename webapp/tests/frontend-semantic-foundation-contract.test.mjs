import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const exists = (path) => existsSync(resolve(root, path))

const main = read('src/main.tsx')
const theme = read('src/theme/nexusTheme.ts')
const provider = read('src/theme/NexusThemeProvider.tsx')
const login = read('src/routes/login.tsx')
const shell = read('src/app/AppShell.tsx')
const workspace = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
const styles = read('src/styles.css')
const a11y = read('src/a11y.css')

test('one MUI theme and provider own the visual foundation', () => {
  assert.match(main, /NexusThemeProvider/)
  assert.match(provider, /<ThemeProvider theme=\{nexusTheme\}>/)
  assert.match(provider, /<CssBaseline \/>/)
  assert.match(theme, /createTheme\(/)
  assert.match(theme, /cssVariables:\s*true/)
  assert.match(theme, /MuiButton:/)
  assert.match(theme, /MuiTextField:/)
  assert.match(theme, /MuiDialog:/)
  assert.match(theme, /minimum|44|44,/)
  assert.match(theme, /prefers-reduced-motion/)
})

test('retired custom visual authorities are physically absent', () => {
  for (const path of [
    'src/components/ui',
    'src/styles/tokens.css',
    'src/styles/components.css',
    'src/styles/auth.css',
    'src/app/app-shell.css',
    'src/features/operator-workspace/operator-workspace.css',
    'src/features/operator-workspace/operator-workspace-refinements.css',
    'src/features/admin-routes/admin-routes.css',
    'src/features/knowledge/knowledge.css',
    'src/features/runtime/runtime-evidence-audit.css',
  ]) {
    assert.equal(exists(path), false, `retired custom visual path returned: ${path}`)
  }
  assert.doesNotMatch(main, /tokens\.css|components\.css|auth\.css/)
})

test('Login is one concise MUI keyboard-complete authentication flow', () => {
  assert.match(login, /from '@mui\/material'/)
  assert.match(login, /component="form"/)
  assert.match(login, /type="submit"/)
  assert.match(login, /aria-pressed=\{showPassword\}/)
  assert.match(login, /severity="error"/)
  assert.match(login, /账号或密码错误。/)
  assert.match(login, /请勿在共享设备保存密码。/)
  assert.doesNotMatch(login, /客服与运营工作台|系统会根据账号权限|无法登录。请检查账号和密码后重试。/)
  assert.match(login, /useState\(''\)/)
  assert.doesNotMatch(login, /useState\('admin'\)/)
  assert.doesNotMatch(login, /navigate\(\{ to: '\/webchat'/)
})

test('application shell and workspace use direct MUI primitives and concise language', () => {
  assert.match(shell, /AppBar/)
  assert.match(shell, /Toolbar/)
  assert.match(shell, /Select/)
  assert.match(workspace, /ListItemButton/)
  assert.match(workspace, /<Tabs/)
  assert.match(workspace, /<Dialog/)
  assert.match(workspace, /处理进度/)
  assert.match(workspace, /待处理任务/)
  assert.doesNotMatch(workspace, /案例处理链路|事实与证据|服务端最终授权/)
  assert.doesNotMatch(shell + workspace, /nd-button|nd-field|nd-badge|operator-workspace\.css/)
})

test('global CSS is bounded to browser and screen-reader foundations', () => {
  assert.doesNotMatch(styles, /--nd-/)
  assert.doesNotMatch(styles, /\.Mui[A-Za-z]/)
  assert.doesNotMatch(styles, /button\s*\{|input\s*\{|textarea\s*\{|select\s*\{/)
  assert.match(a11y, /^\.sr-only\s*\{/)
  assert.doesNotMatch(a11y, /--nd-|\.nd-|\.auth-|\.operator-/)
})

test('dedicated Login browser evidence exists', () => {
  assert.equal(exists('e2e/login-semantic.spec.ts'), true)
})
