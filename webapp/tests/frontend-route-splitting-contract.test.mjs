import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const webchatRoute = read('src/routes/webchat.tsx')
const vite = read('vite.config.ts')
const manifestAssertPath = resolve(root, 'scripts/assert-route-splitting.mjs')
const e2ePath = resolve(root, 'e2e/route-splitting.spec.ts')
const canonicalLazyModules = [
  'src/features/operator-workspace/lazy.tsx',
  'src/features/knowledge/lazy.tsx',
  'src/features/channels/lazy.tsx',
  'src/features/runtime/lazy.tsx',
  'src/features/control-tower/lazy.tsx',
]


test('webchat is a compatibility redirect and no longer mounts a product console', () => {
  assert.match(webchatRoute, /WebchatCompatibilityRedirect/)
  assert.match(webchatRoute, /tab === 'knowledge'/)
  assert.match(webchatRoute, /to: '\/knowledge'/)
  assert.match(webchatRoute, /tab === 'channels'/)
  assert.match(webchatRoute, /to: '\/channels'/)
  assert.match(webchatRoute, /tab === 'runtime'/)
  assert.match(webchatRoute, /to: '\/runtime'/)
  assert.match(webchatRoute, /to: '\/workspace'/)
  assert.doesNotMatch(webchatRoute, /support-console/)
  assert.doesNotMatch(webchatRoute, /LazySupportConsole/)
})


test('canonical product domains own explicit async feature boundaries', () => {
  for (const path of canonicalLazyModules) {
    assert.equal(existsSync(resolve(root, path)), true, `missing canonical lazy module: ${path}`)
  }
  const routes = {
    knowledge: read('src/routes/knowledge.tsx'),
    channels: read('src/routes/channels.tsx'),
    runtime: read('src/routes/runtime.tsx'),
    controlTower: read('src/routes/control-tower.tsx'),
    workspace: read('src/routes/workspace.tsx'),
  }
  assert.match(routes.knowledge, /features\/knowledge\/lazy/)
  assert.match(routes.channels, /features\/channels\/lazy/)
  assert.match(routes.runtime, /features\/runtime\/lazy/)
  assert.match(routes.controlTower, /features\/control-tower\/lazy/)
  assert.match(routes.workspace, /features\/operator-workspace\/lazy/)
  for (const source of Object.values(routes)) {
    assert.match(source, /lazy\(\(\) => import/)
    assert.match(source, /Suspense/)
  }
})


test('production build emits a manifest used for route-closure proof', () => {
  assert.match(vite, /manifest:\s*true/)
  assert.equal(existsSync(manifestAssertPath), true, 'manifest assertion script must exist')
  const assertion = read('scripts/assert-route-splitting.mjs')
  assert.match(assertion, /frontend_dist/)
  assert.match(assertion, /isDynamicEntry/)
  assert.match(assertion, /initial static import closure/)
  assert.match(assertion, /retired Support Console remains in the production manifest/)
  for (const path of canonicalLazyModules) assert.match(assertion, new RegExp(path.replaceAll('/', '\\/').replace('.', '\\.')))
})


test('browser evidence covers protected redirect and canonical destinations', () => {
  assert.equal(existsSync(e2ePath), true, 'route compatibility Playwright evidence must exist')
  const e2e = read('e2e/route-splitting.spec.ts')
  assert.match(e2e, /unauthenticated/)
  assert.match(e2e, /tab=knowledge/)
  assert.match(e2e, /tab=channels/)
  assert.match(e2e, /tab=runtime/)
  assert.doesNotMatch(e2e, /nexus-support-console/)
})
