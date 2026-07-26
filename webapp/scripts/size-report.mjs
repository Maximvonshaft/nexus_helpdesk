import { gzipSync } from 'node:zlib'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const webappDir = fileURLToPath(new URL('..', import.meta.url))
const distDir = join(webappDir, '..', 'frontend_dist')
const manifestPath = join(distDir, '.vite', 'manifest.json')
const singleChunkLimitKb = Number(process.env.WEBAPP_SINGLE_CHUNK_GZIP_MAX_KB || 180)
const firstScreenLimitKb = Number(process.env.WEBAPP_FIRST_SCREEN_JS_GZIP_MAX_KB || 300)

function walk(dir) {
  const files = []
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    const stat = statSync(path)
    if (stat.isDirectory()) files.push(...walk(path))
    else files.push(path)
  }
  return files
}

function gzipKb(path) {
  return gzipSync(readFileSync(path)).length / 1024
}

function fail(message) {
  console.error(`Frontend size report failed: ${message}`)
  process.exit(1)
}

if (!existsSync(distDir)) fail(`build output not found: ${distDir}`)
if (!existsSync(manifestPath)) fail(`Vite manifest not found: ${manifestPath}`)

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const entryPair = Object.entries(manifest).find(([, record]) => record?.isEntry === true)
if (!entryPair) fail('production entry not found in Vite manifest')

const [entryKey] = entryPair
const initialStaticKeys = new Set()
const visitStatic = (key) => {
  if (initialStaticKeys.has(key)) return
  initialStaticKeys.add(key)
  const record = manifest[key]
  if (!record) fail(`manifest import target is missing: ${key}`)
  for (const importedKey of record?.imports || []) visitStatic(importedKey)
}
visitStatic(entryKey)

const initialStaticFiles = new Set(
  [...initialStaticKeys]
    .map((key) => manifest[key]?.file)
    .filter((file) => typeof file === 'string' && file.endsWith('.js')),
)

const jsFiles = walk(distDir).filter((path) => path.endsWith('.js'))
const rows = jsFiles.map((path) => ({ path: relative(distDir, path).replaceAll('\\', '/'), gzipKb: gzipKb(path) }))
rows.sort((a, b) => b.gzipKb - a.gzipKb)

const largest = rows[0]
const firstScreenRows = rows.filter((row) => initialStaticFiles.has(row.path))
const firstScreen = firstScreenRows.reduce((total, row) => total + row.gzipKb, 0)

console.log(JSON.stringify({
  ok: true,
  singleChunkLimitKb,
  firstScreenLimitKb,
  largest,
  firstScreenGzipKb: Number(firstScreen.toFixed(2)),
  initialStaticFiles: firstScreenRows.map((row) => ({ path: row.path, gzipKb: Number(row.gzipKb.toFixed(2)) })),
  files: rows.map((row) => ({ path: row.path, gzipKb: Number(row.gzipKb.toFixed(2)) })),
}, null, 2))

if (largest && largest.gzipKb > singleChunkLimitKb) {
  fail(`largest JavaScript chunk exceeds gzip budget: ${largest.path} ${largest.gzipKb.toFixed(2)}KB > ${singleChunkLimitKb}KB`)
}

if (firstScreen > firstScreenLimitKb) {
  fail(`initial static JavaScript exceeds gzip budget: ${firstScreen.toFixed(2)}KB > ${firstScreenLimitKb}KB`)
}
