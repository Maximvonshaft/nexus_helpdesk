import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')

function read(relativePath) {
  const path = join(WEBAPP_ROOT, relativePath)
  assert.equal(existsSync(path), true, `missing required frontend path: ${relativePath}`)
  return readFileSync(path, 'utf8')
}

test('workspace route consumes the server-owned current scope projection', () => {
  const route = read('src/routes/workspace.tsx')
  const api = read('src/lib/operatorWorkspaceApi.ts')
  const types = read('src/lib/operatorWorkspaceTypes.ts')

  assert.match(api, /\/api\/admin\/operator-queue\/my-scopes/)
  assert.match(api, /currentScopes:/)
  assert.match(route, /operatorWorkspaceApi\.currentScopes/)
  assert.match(route, /scope=\{workspaceScopeFromAuthorized\(selectedScope\)\}/)
  assert.doesNotMatch(route, /loadWorkspaceScope|saveWorkspaceScope|LegacyWorkspaceFallback/)
  assert.match(types, /AuthorizedWorkspaceScopesResponse/)
  assert.match(types, /workspaceScopeFromAuthorized/)
  assert.doesNotMatch(route, /tenantKey:\s*['"][^'"]+['"]/)
})

test('one MUI application shell owns product identity, navigation, session and scope selection', () => {
  const shell = read('src/app/AppShell.tsx')
  const navigation = read('src/app/navigation.ts')
  const navigationView = read('src/app/AppNavigation.tsx')
  const theme = read('src/theme/nexusTheme.ts')
  const provider = read('src/theme/NexusThemeProvider.tsx')

  assert.match(shell, /Nexus OSR/)
  assert.doesNotMatch(shell, /客服与运营工作台/)
  assert.match(shell, /工作范围/)
  assert.match(shell, /跳到主要内容/)
  assert.match(shell, /scope\.country_code/)
  assert.match(shell, /channelPresentation\(scope\.channel_key\)\.label/)
  assert.match(shell, /<AppBar/)
  assert.match(shell, /<Toolbar/)
  assert.match(shell, /to="\/account"/)
  assert.match(navigationView, /APP_NAVIGATION\.filter/)
  assert.match(navigationView, /from '@mui\/material'/)
  assert.match(navigationView, /from '@tanstack\/react-router'/)
  for (const route of ['/workspace', '/knowledge', '/channels', '/runtime', '/control-tower', '/administration']) {
    assert.match(navigation, new RegExp(route.replace('/', '\\/')))
  }
  assert.match(theme, /MuiButton:/)
  assert.match(theme, /minHeight:\s*44/)
  assert.match(theme, /prefers-reduced-motion/)
  assert.match(provider, /<ThemeProvider theme=\{nexusTheme\}>/)
  assert.match(provider, /<CssBaseline \/>/)
  assert.equal(existsSync(join(WEBAPP_ROOT, 'src', 'app', 'app-shell.css')), false)
  assert.doesNotMatch(shell, />\s*\{scope\.tenant_key\}\s*</)
  assert.doesNotMatch(shell, />\s*\{scope\.tenant_hash\}\s*</)
})

test('normal operators fail closed with concise recovery copy when no scope exists', () => {
  const route = read('src/routes/workspace.tsx')
  assert.match(route, /未分配工作范围/)
  assert.match(route, /请联系管理员。/)
  assert.match(route, /无法读取工作范围/)
  assert.match(route, /请重新加载。/)
  assert.doesNotMatch(route, /系统不会自动扩大|手工猜测|服务端授权/)
  assert.doesNotMatch(route, /requires_explicit_admin_scope|LegacyWorkspaceFallback/)
})

test('canonical shell has one theme authority and no route stylesheet authority', () => {
  const main = read('src/main.tsx')
  const sourcePaths = [
    'src/styles/tokens.css',
    'src/styles/components.css',
    'src/app/app-shell.css',
  ]
  for (const path of sourcePaths) assert.equal(existsSync(join(WEBAPP_ROOT, path)), false)
  assert.equal((main.match(/<NexusThemeProvider>/g) ?? []).length, 1)
  assert.equal((main.match(/from '@\/theme\/NexusThemeProvider'/g) ?? []).length, 1)
  assert.doesNotMatch(main, /tokens\.css|components\.css|app-shell\.css/)
})
