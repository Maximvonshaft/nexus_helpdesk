import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const webappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(webappRoot, '..')
const read = (relative) => fs.readFileSync(path.join(webappRoot, relative), 'utf8')

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
    'src/features/operator-workspace/operator-workspace.css',
    'src/features/operator-workspace/operator-workspace-refinements.css',
    'src/features/admin-routes/admin-routes.css',
    'src/features/knowledge/knowledge.css',
    'src/features/runtime/runtime-evidence-audit.css',
  ]) {
    assert.equal(fs.existsSync(path.join(webappRoot, relative)), false, relative)
  }
  assert.equal(fs.existsSync(path.join(repositoryRoot, 'frontend')), false)
})

test('workspace has one shell and input-bound cancel preview', () => {
  const source = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
  assert.doesNotMatch(source, /function\s+AppNavigation\b/)
  assert.doesNotMatch(source, /operator-app-header/)
  assert.doesNotMatch(source, /\/webchat\?tab=/)
  assert.match(source, /type CancelPreviewBinding/)
  assert.match(source, /ticketId[\s\S]*waybill[\s\S]*caller[\s\S]*reasonCode/)
  assert.match(source, /cancelPreview\.fingerprint !== currentCancelFingerprint/)
  assert.match(source, /案例处理链路/)
})

test('MUI and operational status modules are the only visual and state authorities', () => {
  const theme = read('src/theme/nexusTheme.ts')
  const provider = read('src/theme/NexusThemeProvider.tsx')
  const status = read('src/domain/operationalPresentation.ts')
  const supportStatus = read('src/lib/supportStatus.ts')
  const workspaceStatus = read('src/lib/operatorWorkspacePresentation.ts')
  assert.match(theme, /createTheme\(/)
  assert.match(theme, /MuiButton:/)
  assert.match(theme, /MuiDialog:/)
  assert.match(provider, /<ThemeProvider theme=\{nexusTheme\}>/)
  assert.match(provider, /<CssBaseline \/>/)
  assert.match(status, /technical_complete/)
  assert.match(status, /operational_complete/)
  assert.match(status, /customer_notified/)
  assert.match(supportStatus, /operationalPresentation\(status, message\)/)
  assert.match(workspaceStatus, /return operationalPresentation\(statusValue, messageValue\)/)
})

test('runtime and knowledge permissions have separate read and write projections', () => {
  const runtimeRoute = read('src/routes/runtime.tsx')
  const knowledgeRoute = read('src/routes/knowledge.tsx')
  assert.match(runtimeRoute, /runtime\.manage/)
  assert.match(runtimeRoute, /audit\.read/)
  assert.match(knowledgeRoute, /KnowledgeReadOnlyPage/)
  assert.match(knowledgeRoute, /ai_config\.manage/)
})

test('control tower accepts only canonical hrefs in the browser', () => {
  const source = read('src/features/control-tower/ControlTowerPage.tsx')
  assert.match(source, /canonicalAppHref/)
  assert.doesNotMatch(source, /\/accounts/)
  assert.doesNotMatch(source, /\/outbound-email/)
  assert.doesNotMatch(source, /\/ai-control/)
})

test('dependency graph selects MUI and excludes retired or parallel visual packages', () => {
  const manifest = JSON.parse(read('package.json'))
  assert.equal(manifest.dependencies?.['@mui/material'], '9.2.0')
  assert.equal(manifest.dependencies?.['@mui/icons-material'], '9.2.0')
  assert.equal(manifest.dependencies?.['@emotion/react'], '11.14.0')
  assert.equal(manifest.dependencies?.['@emotion/styled'], '11.14.1')
  assert.equal(manifest.dependencies?.['react-is'], '18.3.1')
  assert.equal(manifest.overrides?.['react-is'], '18.3.1')
  for (const dependency of [
    '@radix-ui/react-dialog',
    '@radix-ui/react-dropdown-menu',
    '@radix-ui/react-popover',
    '@radix-ui/react-select',
    '@radix-ui/react-tabs',
    '@radix-ui/react-tooltip',
    '@chakra-ui/react',
    '@mantine/core',
    'antd',
    'tailwindcss',
    'bootstrap',
    'clsx',
    'livekit-client',
  ]) {
    assert.equal(manifest.dependencies?.[dependency], undefined, dependency)
  }
})
