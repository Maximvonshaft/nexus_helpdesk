import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')

function staticNavTargets(source) {
  return [...source.matchAll(/\{\s*to:\s*'([^']+)'/g)].map((match) => match[1])
}

function registeredStaticRoutes(source) {
  return [...source.matchAll(/path:\s*'([^'$]+)'/g)].map((match) => match[1])
}

test('provider credentials nav route is registered in router', () => {
  assert.match(appShell, /to: '\/provider-credentials'/)
  assert.match(router, /ProviderCredentialsRoute/)
  assert.match(router, /@\/routes\/provider-credentials/)
})

test('internal webcall routes are intentionally classified', () => {
  assert.match(router, /Internal operator console for human WebCall handling/)
  assert.match(router, /Internal ops-only AI sandbox/)
  assert.match(appShell, /to: '\/webcall-ai-demo'[\s\S]*permission: 'ops'/)
  assert.doesNotMatch(appShell, /to: '\/webchat-voice'/)
})

test('primary nav internal hrefs have matching registered routes', () => {
  const routeFiles = [
    'login.tsx',
    'index.tsx',
    'workspace.tsx',
    'webchat.tsx',
    'runtime.tsx',
    'webcall-ai-demo.tsx',
    'provider-credentials.tsx',
    'accounts.tsx',
    'bulletins.tsx',
    'ai-control.tsx',
    'control-plane.tsx',
    'users.tsx',
  ]
  const registered = new Set(routeFiles.flatMap((file) => registeredStaticRoutes(readFileSync(resolve(root, `src/routes/${file}`), 'utf8'))))
  const missing = staticNavTargets(appShell).filter((target) => target.startsWith('/') && !registered.has(target))
  assert.deepEqual(missing, [])
})
