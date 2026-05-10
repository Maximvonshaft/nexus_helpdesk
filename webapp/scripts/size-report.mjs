import { gzipSync } from 'node:zlib'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { existsSync } from 'node:fs'
import { join, relative } from 'node:path'

const distDir = new URL('../../frontend_dist', import.meta.url).pathname
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

if (!existsSync(distDir)) {
  console.error(`Build output not found: ${distDir}`)
  process.exit(1)
}

const jsFiles = walk(distDir).filter((path) => path.endsWith('.js'))
const rows = jsFiles.map((path) => ({ path: relative(distDir, path), gzipKb: gzipKb(path) }))
rows.sort((a, b) => b.gzipKb - a.gzipKb)

const largest = rows[0]
const firstScreen = rows
  .filter((row) => !/lazy|route|vendor/i.test(row.path))
  .reduce((total, row) => total + row.gzipKb, 0)

console.log(JSON.stringify({
  ok: true,
  singleChunkLimitKb,
  firstScreenLimitKb,
  largest,
  firstScreenGzipKb: Number(firstScreen.toFixed(2)),
  files: rows.map((row) => ({ path: row.path, gzipKb: Number(row.gzipKb.toFixed(2)) })),
}, null, 2))

if (largest && largest.gzipKb > singleChunkLimitKb) {
  console.error(`Largest JS chunk exceeds gzip budget: ${largest.path} ${largest.gzipKb.toFixed(2)}KB > ${singleChunkLimitKb}KB`)
  process.exit(1)
}

if (firstScreen > firstScreenLimitKb) {
  console.error(`First-screen JS exceeds gzip budget: ${firstScreen.toFixed(2)}KB > ${firstScreenLimitKb}KB`)
  process.exit(1)
}
