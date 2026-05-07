import { gzipSync } from 'node:zlib'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const dist = path.resolve(root, '../frontend_dist/assets')
const singleChunkBudget = Number(process.env.FRONTEND_SINGLE_CHUNK_GZIP_BUDGET_KB || 180) * 1024
const firstScreenBudget = Number(process.env.FRONTEND_FIRST_SCREEN_GZIP_BUDGET_KB || 300) * 1024

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    const stat = statSync(full)
    if (stat.isDirectory()) out.push(...walk(full))
    else out.push(full)
  }
  return out
}

if (!existsSync(dist)) {
  console.error(`[size-report] assets directory not found: ${dist}`)
  process.exit(1)
}

const jsFiles = walk(dist).filter((file) => file.endsWith('.js'))
const rows = jsFiles.map((file) => {
  const raw = readFileSync(file)
  const gzipBytes = gzipSync(raw).byteLength
  return {
    file: path.relative(path.resolve(root, '../frontend_dist'), file),
    rawBytes: raw.byteLength,
    gzipBytes,
  }
}).sort((a, b) => b.gzipBytes - a.gzipBytes)

let failed = false
let firstScreenGzip = 0
console.log('Frontend JS gzip size report')
console.log('file\traw_kb\tgzip_kb\tstatus')
for (const row of rows) {
  const status = row.gzipBytes > singleChunkBudget ? 'OVER_SINGLE_CHUNK_BUDGET' : 'ok'
  if (status !== 'ok') failed = true
  // Conservative first-screen approximation: entry/index/react/router/vendor
  // chunks are normally needed before the operator console can become useful.
  if (/index|main|react-vendor|router-query|vendor/.test(row.file)) firstScreenGzip += row.gzipBytes
  console.log(`${row.file}\t${(row.rawBytes / 1024).toFixed(1)}\t${(row.gzipBytes / 1024).toFixed(1)}\t${status}`)
}

console.log(`first_screen_estimated_gzip_kb\t${(firstScreenGzip / 1024).toFixed(1)}\tbudget_kb\t${(firstScreenBudget / 1024).toFixed(0)}`)
if (firstScreenGzip > firstScreenBudget) {
  console.error('[size-report] first-screen JS gzip budget exceeded')
  failed = true
}
if (failed) process.exit(1)
