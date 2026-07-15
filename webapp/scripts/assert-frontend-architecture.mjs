#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const webappRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..')
const repositoryRoot = path.resolve(webappRoot, '..')
const srcRoot = path.join(webappRoot, 'src')
const entrypoint = path.join(srcRoot, 'main.tsx')

const SOURCE_EXTENSIONS = ['.ts', '.tsx', '.css']
const IMPORT_RE = /(?:import|export)\s+(?:[^'"()]*?\s+from\s+)?["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)/g
const PRIMITIVE_EXPORT_RE = /export\s+(?:const|function|class)\s+(Button|Badge|Card|Field|ConfirmDialog)\b/g

const forbiddenPaths = [
  path.join(repositoryRoot, 'frontend'),
  path.join(srcRoot, 'features', 'support-console'),
  path.join(srcRoot, 'shared', 'ui'),
  path.join(srcRoot, 'shared', 'api'),
  path.join(srcRoot, 'lib', 'api.ts'),
]

function walk(directory) {
  if (!fs.existsSync(directory)) return []
  const files = []
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name)
    if (entry.isDirectory()) files.push(...walk(absolute))
    else files.push(absolute)
  }
  return files
}

function normalize(value) {
  return path.normalize(value)
}

function sourceFiles() {
  return walk(srcRoot)
    .filter((file) => SOURCE_EXTENSIONS.includes(path.extname(file)))
    .filter((file) => !file.endsWith('.d.ts'))
    .map(normalize)
}

function resolveImport(importer, specifier) {
  if (specifier.startsWith('@/')) return resolveCandidate(path.join(srcRoot, specifier.slice(2)))
  if (specifier.startsWith('.')) return resolveCandidate(path.resolve(path.dirname(importer), specifier))
  return null
}

function resolveCandidate(candidate) {
  const candidates = [
    candidate,
    ...SOURCE_EXTENSIONS.map((extension) => `${candidate}${extension}`),
    ...SOURCE_EXTENSIONS.map((extension) => path.join(candidate, `index${extension}`)),
  ]
  return candidates.find((file) => fs.existsSync(file) && fs.statSync(file).isFile()) ?? null
}

function importsFor(file) {
  const content = fs.readFileSync(file, 'utf8')
  const imports = []
  for (const match of content.matchAll(IMPORT_RE)) {
    const resolved = resolveImport(file, match[1] ?? match[2])
    if (resolved) imports.push(normalize(resolved))
  }
  return imports
}

function reachableFiles() {
  const reachable = new Set()
  const queue = [normalize(entrypoint)]
  while (queue.length) {
    const current = queue.pop()
    if (!current || reachable.has(current) || !fs.existsSync(current)) continue
    reachable.add(current)
    for (const imported of importsFor(current)) {
      if (!reachable.has(imported)) queue.push(imported)
    }
  }
  return reachable
}

function relative(file) {
  return path.relative(repositoryRoot, file).split(path.sep).join('/')
}

function duplicatePrimitiveAuthorities(files) {
  const owners = new Map()
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    for (const match of content.matchAll(PRIMITIVE_EXPORT_RE)) {
      const primitive = match[1]
      const entries = owners.get(primitive) ?? []
      entries.push(relative(file))
      owners.set(primitive, entries)
    }
  }
  return [...owners.entries()]
    .filter(([, entries]) => entries.length > 1)
    .map(([primitive, entries]) => `${primitive}: ${entries.join(', ')}`)
}

function assertCanonicalNavigation(files, failures) {
  const navigationOwners = files
    .filter((file) => /\.(?:ts|tsx)$/.test(file))
    .filter((file) => fs.readFileSync(file, 'utf8').includes('APP_NAVIGATION'))
    .map(relative)
  const allowed = new Set(['webapp/src/app/navigation.ts', 'webapp/src/app/AppNavigation.tsx'])
  const unexpected = navigationOwners.filter((file) => !allowed.has(file))
  if (unexpected.length) failures.push(`unexpected navigation authority: ${unexpected.join(', ')}`)

  const workspace = path.join(srcRoot, 'features', 'operator-workspace', 'OperatorWorkspacePage.tsx')
  if (fs.existsSync(workspace)) {
    const content = fs.readFileSync(workspace, 'utf8')
    if (/function\s+AppNavigation\b/.test(content) || /className=["']operator-app-header["']/.test(content)) {
      failures.push('OperatorWorkspacePage still owns a second application shell or navigation')
    }
    if (content.includes('/webchat?tab=')) failures.push('OperatorWorkspacePage still links through compatibility tabs')
  }
}

function assertTransportAuthority(files, failures) {
  const genericFetchOwners = []
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    if (/\bfetch\s*\(/.test(content) || /new\s+AbortController\s*\(/.test(content)) {
      genericFetchOwners.push(relative(file))
    }
  }
  const unexpected = genericFetchOwners.filter((file) => file !== 'webapp/src/lib/apiClient.ts')
  if (unexpected.length) failures.push(`generic HTTP transport outside apiClient.ts: ${unexpected.join(', ')}`)
}

const failures = []
for (const forbidden of forbiddenPaths) {
  if (fs.existsSync(forbidden)) failures.push(`retired path exists: ${relative(forbidden)}`)
}

const files = sourceFiles()
const reachable = reachableFiles()
const unreachable = files.filter((file) => !reachable.has(file)).map(relative)
if (unreachable.length) failures.push(`unreachable production files: ${unreachable.join(', ')}`)

const duplicatePrimitives = duplicatePrimitiveAuthorities(files)
if (duplicatePrimitives.length) failures.push(`duplicate UI authorities: ${duplicatePrimitives.join(' | ')}`)

assertCanonicalNavigation(files, failures)
assertTransportAuthority(files, failures)

if (failures.length) {
  console.error(JSON.stringify({ ok: false, failures }, null, 2))
  process.exit(1)
}

console.log(JSON.stringify({
  ok: true,
  production_files: files.length,
  reachable_files: reachable.size,
  canonical_entrypoint: relative(entrypoint),
}, null, 2))
