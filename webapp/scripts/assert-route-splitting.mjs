import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const webappDir = fileURLToPath(new URL('..', import.meta.url))
const distDir = join(webappDir, '..', 'frontend_dist')
const manifestPath = join(distDir, '.vite', 'manifest.json')
const canonicalDynamicSources = [
  'src/features/operator-workspace/lazy.tsx',
  'src/features/knowledge/lazy.tsx',
  'src/features/channels/lazy.tsx',
  'src/features/runtime/lazy.tsx',
  'src/features/control-tower/lazy.tsx',
]

function fail(message) {
  console.error(`Route splitting assertion failed: ${message}`)
  process.exit(1)
}

if (!existsSync(manifestPath)) fail(`missing Vite manifest: ${manifestPath}`)

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const records = Object.entries(manifest)
const entryPair = records.find(([, record]) => record?.isEntry === true)
if (!entryPair) fail('no production entry was found')

for (const [key, record] of records) {
  const source = String(record?.src || key).replaceAll('\\', '/')
  if (source.includes('features/support-console/')) {
    fail(`retired Support Console product code remains in the production manifest: ${source}`)
  }
}

const [entryKey] = entryPair
const staticClosure = new Set()
const visitStatic = (key) => {
  if (staticClosure.has(key)) return
  staticClosure.add(key)
  const record = manifest[key]
  for (const importedKey of record?.imports || []) visitStatic(importedKey)
}
visitStatic(entryKey)

const reachableDynamicTargets = new Set()
const visitDynamicTargets = (key) => {
  const record = manifest[key]
  for (const dynamicKey of record?.dynamicImports || []) {
    if (reachableDynamicTargets.has(dynamicKey)) continue
    reachableDynamicTargets.add(dynamicKey)
    visitDynamicTargets(dynamicKey)
  }
  for (const importedKey of record?.imports || []) visitDynamicTargets(importedKey)
}
visitDynamicTargets(entryKey)

const verified = []
for (const sourceSuffix of canonicalDynamicSources) {
  const pair = records.find(([key, record]) => {
    const source = String(record?.src || key).replaceAll('\\', '/')
    return source.endsWith(sourceSuffix)
  })
  if (!pair) fail(`no manifest record found for ${sourceSuffix}`)
  const [key, record] = pair
  if (record.isDynamicEntry !== true) fail(`${key} is not marked as isDynamicEntry`)
  if (staticClosure.has(key)) fail(`${sourceSuffix} is present in the initial static import closure`)
  if (!reachableDynamicTargets.has(key)) fail(`${sourceSuffix} is not reachable from the application entry`)
  if (!existsSync(join(distDir, record.file))) fail(`missing route asset: ${record.file}`)
  verified.push({ source: sourceSuffix, file: record.file, css: record.css || [] })
}

console.log(JSON.stringify({
  ok: true,
  entryKey,
  initialStaticModules: staticClosure.size,
  canonicalDynamicRoutes: verified,
}, null, 2))
