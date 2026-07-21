import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { resolve, relative } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const exists = (path) => existsSync(resolve(root, path))
const walk = (dir) => {
  if (!exists(dir)) return []
  const absolute = resolve(root, dir)
  return readdirSync(absolute).flatMap((entry) => {
    const full = resolve(absolute, entry)
    const rel = relative(root, full).replaceAll('\\', '/')
    return statSync(full).isDirectory() ? walk(rel) : [rel]
  })
}


test('legacy frontend entrypoint is physically retired', () => {
  assert.equal(exists('../frontend'), false)
})

test('legacy URL preservation is exactly the approved redirect set', () => {
  const rootRoute = read('src/routes/root.tsx')
  const approved = {
    '/dashboard': '/control-tower',
    '/inbox': '/workspace',
    '/tickets': '/workspace',
    '/knowledge-ai': '/knowledge',
    '/whatsapp': '/channels',
    '/accounts': '/channels',
    '/ai-runtime': '/runtime',
  }
  const matches = [...rootRoute.matchAll(/window\.location\.replace\(`\/\#\$\{path\.replace\(([^)]+)\)\}\$\{window\.location\.search\}\$\{window\.location\.hash\}`\)/g)]
  const routeLines = rootRoute.split('\n').filter((line) => line.includes("path.replace('") || line.includes('path.replace("'))
  assert.equal(matches.length, 0, 'hash-preserving redirect machinery must stay retired')
  assert.equal(routeLines.length, 0, 'regex-style redirect replacement must stay retired')
  for (const [legacy, current] of Object.entries(approved)) {
    assert.match(rootRoute, new RegExp(`'${legacy}': '${current}'`))
  }
  assert.equal((rootRoute.match(/legacyRedirects:/g) || []).length, 1)
  assert.doesNotMatch(rootRoute, /\/ticket\/\$|\/case\/\$|startsWith\('\/ticket|startsWith\('\/case/)
})

test('canonical routes do not reintroduce a second application shell', () => {
  const routeFiles = walk('src/routes').filter((path) => path.endsWith('.tsx'))
  const allowed = new Set([
    'src/routes/account.tsx',
    'src/routes/administration.tsx',
    'src/routes/agent-control.tsx',
    'src/routes/channels.tsx',
    'src/routes/control-tower.tsx',
    'src/routes/index.tsx',
    'src/routes/knowledge.tsx',
    'src/routes/login.tsx',
    'src/routes/root.tsx',
    'src/routes/runtime.tsx',
    'src/routes/webchat.tsx',
    'src/routes/workspace.tsx',
  ])
  assert.deepEqual(new Set(routeFiles), allowed)
  for (const path of routeFiles) {
    if (path.endsWith('/root.tsx') || path.endsWith('/login.tsx')) continue
    const source = read(path)
    assert.doesNotMatch(source, /AppShell|Drawer|AppBar|NexusThemeProvider/)
  }
})

test('root owns the only authenticated application shell and route tree', () => {
  const rootRoute = read('src/routes/root.tsx')
  assert.match(rootRoute, /AppShell/)
  assert.match(rootRoute, /NexusThemeProvider/)
  assert.equal((rootRoute.match(/createRootRoute/g) || []).length, 1)
  assert.equal((rootRoute.match(/Route\.addChildren/g) || []).length, 1)
  assert.doesNotMatch(rootRoute, /routeTree\.gen/)
})

test('visual inventory remains under the canonical component and style budget', () => {
  const packageJson = JSON.parse(read('package.json'))
  const sourceFiles = walk('src')
  const scriptFiles = sourceFiles.filter((path) => /\.(ts|tsx|js|jsx)$/.test(path))
  const imports = scriptFiles.map(read).join('\n')
  const bannedPackages = [
    '@chakra-ui',
    'antd',
    'bootstrap',
    'styled-components',
    'tailwindcss',
  ]
  for (const name of bannedPackages) {
    assert.equal(Boolean(packageJson.dependencies?.[name] || packageJson.devDependencies?.[name]), false, `${name} is not allowed`)
  }
  assert.doesNotMatch(imports, /from ['"]@\/components\/ui\//)
  assert.equal(exists('src/components/ui'), false)
  assert.equal(exists('src/styles'), false)
  assert.equal(sourceFiles.filter((path) => path.endsWith('.css')).length, 0)
  assert.equal(sourceFiles.filter((path) => path.endsWith('.module.css')).length, 0)
})

test('shared state and notification authority are singular', () => {
  const packageJson = JSON.parse(read('package.json'))
  assert.equal(Boolean(packageJson.dependencies?.zustand), false)
  assert.equal(Boolean(packageJson.dependencies?.redux), false)
  assert.equal(Boolean(packageJson.dependencies?.jotai), false)
  assert.equal(Boolean(packageJson.dependencies?.recoil), false)
  assert.equal(Boolean(packageJson.dependencies?.['react-hot-toast']), false)
  assert.equal(Boolean(packageJson.dependencies?.notistack), false)

  const sources = walk('src').filter((path) => /\.(ts|tsx)$/.test(path)).map(read).join('\n')
  assert.doesNotMatch(sources, /createContext\(|useContext\(/)
  assert.equal((sources.match(/<Snackbar/g) || []).length, 1)
})

test('UI never evaluates authorization from raw role names', () => {
  const files = walk('src').filter((path) => /\.(ts|tsx)$/.test(path))
  const violations = []
  for (const path of files) {
    const source = read(path)
    if (/\.role\s*(?:===|!==|==|!=)|role\s*===|role\s*!==|\['admin'|\["admin"|includes\([^)]*role/i.test(source)) {
      violations.push(path)
    }
  }
  assert.deepEqual(violations, [])
})

test('feature code cannot call axios or fetch directly', () => {
  const files = walk('src').filter((path) => /\.(ts|tsx)$/.test(path))
  const allowed = new Set(['src/lib/apiClient.ts'])
  const violations = []
  for (const path of files) {
    if (allowed.has(path)) continue
    const source = read(path)
    if (/\baxios\b|\bfetch\s*\(/.test(source)) violations.push(path)
  }
  assert.deepEqual(violations, [])
})
