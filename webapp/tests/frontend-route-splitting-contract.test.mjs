import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const route = read('src/routes/webchat.tsx')
const vite = read('vite.config.ts')
const lazyPath = resolve(root, 'src/features/support-console/lazy.tsx')
const manifestAssertPath = resolve(root, 'scripts/assert-route-splitting.mjs')
const e2ePath = resolve(root, 'e2e/route-splitting.spec.ts')


test('webchat route defines a real async Support Console boundary', () => {
  assert.match(route, /lazy\(\(\) => import\(['"]@\/features\/support-console\/lazy['"]\)\)/)
  assert.match(route, /<Suspense/)
  assert.match(route, /aria-live="polite"/)
  assert.match(route, /加载运营工作台中…/)
  assert.doesNotMatch(route, /import \{ SupportConsolePage \} from/)
  assert.doesNotMatch(route, /import ['"]@\/features\/support-console\/support-console\.css['"]/)
})


test('async module owns the Support Console implementation and CSS', () => {
  assert.equal(existsSync(lazyPath), true, 'lazy Support Console module must exist')
  const lazyModule = read('src/features/support-console/lazy.tsx')
  assert.match(lazyModule, /import ['"]\.\/support-console\.css['"]/)
  assert.match(lazyModule, /SupportConsolePage/)
  assert.match(lazyModule, /export default/)
})


test('production build emits a manifest used for static-closure proof', () => {
  assert.match(vite, /manifest:\s*true/)
  assert.equal(existsSync(manifestAssertPath), true, 'manifest assertion script must exist')
  const assertion = read('scripts/assert-route-splitting.mjs')
  assert.match(assertion, /frontend_dist/)
  assert.match(assertion, /isDynamicEntry/)
  assert.match(assertion, /initial static import closure/)
  assert.match(assertion, /support-console\/lazy\.tsx/)
})


test('browser evidence covers protected redirect, loading fallback, and console completion', () => {
  assert.equal(existsSync(e2ePath), true, 'route splitting Playwright evidence must exist')
  const e2e = read('e2e/route-splitting.spec.ts')
  assert.match(e2e, /unauthenticated/)
  assert.match(e2e, /加载运营工作台中…/)
  assert.match(e2e, /nexus-support-console/)
})
