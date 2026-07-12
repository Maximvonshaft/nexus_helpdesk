import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const webappDir = fileURLToPath(new URL('..', import.meta.url))
const distDir = join(webappDir, '..', 'frontend_dist')
const manifestPath = join(distDir, '.vite', 'manifest.json')
const supportSourceSuffix = 'src/features/support-console/lazy.tsx'

function fail(message) {
  console.error(`Route splitting assertion failed: ${message}`)
  process.exit(1)
}

if (!existsSync(manifestPath)) fail(`missing Vite manifest: ${manifestPath}`)

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const records = Object.entries(manifest)
const entryPair = records.find(([, record]) => record?.isEntry === true)
const supportPair = records.find(([key, record]) => {
  const source = String(record?.src || key).replaceAll('\\', '/')
  return source.endsWith(supportSourceSuffix)
})

if (!entryPair) fail('no production entry was found')
if (!supportPair) fail(`no manifest record found for ${supportSourceSuffix}`)

const [entryKey] = entryPair
const [supportKey, supportRecord] = supportPair
if (supportRecord.isDynamicEntry !== true) {
  fail(`${supportKey} is not marked as isDynamicEntry`)
}

const staticClosure = new Set()
const visitStatic = (key) => {
  if (staticClosure.has(key)) return
  staticClosure.add(key)
  const record = manifest[key]
  for (const importedKey of record?.imports || []) visitStatic(importedKey)
}
visitStatic(entryKey)

if (staticClosure.has(supportKey)) {
  fail('Support Console is present in the initial static import closure')
}

const dynamicTargets = new Set()
for (const key of staticClosure) {
  for (const dynamicKey of manifest[key]?.dynamicImports || []) dynamicTargets.add(dynamicKey)
}
if (!dynamicTargets.has(supportKey)) {
  fail('Support Console dynamic entry is not reachable from the application entry')
}

if (!Array.isArray(supportRecord.css) || supportRecord.css.length === 0) {
  fail('Support Console dynamic entry does not own an async CSS asset')
}

const supportAsset = join(distDir, supportRecord.file)
if (!existsSync(supportAsset)) fail(`missing Support Console asset: ${supportRecord.file}`)
for (const cssAsset of supportRecord.css) {
  if (!existsSync(join(distDir, cssAsset))) fail(`missing Support Console CSS asset: ${cssAsset}`)
}

console.log(JSON.stringify({
  ok: true,
  entryKey,
  supportKey,
  supportFile: supportRecord.file,
  supportCss: supportRecord.css,
  initialStaticModules: staticClosure.size,
}, null, 2))
