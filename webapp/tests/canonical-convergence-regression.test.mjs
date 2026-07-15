import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const webappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(webappRoot, '..')
const read = (relative) => fs.readFileSync(path.join(webappRoot, relative), 'utf8')


test('retired frontend authorities are physically absent', () => {
  for (const relative of [
    'src/features/support-console',
    'src/shared/ui',
    'src/shared/api',
    'src/lib/api.ts',
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
})


test('canonical button and status modules are the only shared authorities', () => {
  const button = read('src/components/ui/Button.tsx')
  const status = read('src/domain/operationalPresentation.ts')
  const supportStatus = read('src/lib/supportStatus.ts')
  const workspaceStatus = read('src/lib/operatorWorkspacePresentation.ts')
  assert.match(button, /leadingIcon/)
  assert.match(button, /loadingLabel/)
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


test('dependency graph excludes retired UI and voice packages', () => {
  const manifest = JSON.parse(read('package.json'))
  for (const dependency of [
    '@radix-ui/react-dropdown-menu',
    '@radix-ui/react-popover',
    '@radix-ui/react-select',
    '@radix-ui/react-tabs',
    '@radix-ui/react-tooltip',
    'clsx',
    'livekit-client',
  ]) {
    assert.equal(manifest.dependencies?.[dependency], undefined, dependency)
  }
})
