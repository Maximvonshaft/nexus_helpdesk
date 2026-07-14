import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { extname, join, relative, resolve } from 'node:path'

const webappRoot = resolve(import.meta.dirname, '..')
const srcRoot = join(webappRoot, 'src')

function walk(root) {
  if (!existsSync(root)) return []
  const files = []
  for (const name of readdirSync(root)) {
    const path = join(root, name)
    const stat = statSync(path)
    if (stat.isDirectory()) files.push(...walk(path))
    else files.push(path)
  }
  return files
}

function read(path) {
  return readFileSync(path, 'utf8')
}

function relativePath(path) {
  return relative(webappRoot, path).replaceAll('\\', '/')
}

const codeFiles = walk(srcRoot).filter((path) => ['.ts', '.tsx'].includes(extname(path)))
const cssFiles = walk(srcRoot).filter((path) => path.endsWith('.css'))
const indexHtml = read(join(webappRoot, 'index.html'))
const usageCorpus = [indexHtml, ...codeFiles.map(read)].join('\n')
const dynamicPrefixes = new Set()
for (const match of usageCorpus.matchAll(/([A-Za-z_][\w-]*--|[A-Za-z_][\w-]*-)\$\{/g)) dynamicPrefixes.add(match[1])

const definitions = new Map()
for (const path of cssFiles) {
  const source = read(path)
  assert.doesNotMatch(source, /transition(?:-property)?\s*:\s*all\b/i, `transition: all is prohibited: ${relativePath(path)}`)
  for (const match of source.matchAll(/\.([A-Za-z_][\w-]*)/g)) {
    const className = match[1]
    const locations = definitions.get(className) ?? new Set()
    locations.add(relativePath(path))
    definitions.set(className, locations)
  }
}
assert.doesNotMatch(indexHtml, /(?:user-scalable\s*=\s*no|maximum-scale\s*=\s*1)/i, 'viewport must not disable zoom')

function isUsed(className) {
  const escaped = className.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const exact = new RegExp(`(^|[^A-Za-z0-9_-])${escaped}([^A-Za-z0-9_-]|$)`)
  if (exact.test(usageCorpus)) return true
  return [...dynamicPrefixes].some((prefix) => className.startsWith(prefix))
}

const unusedClasses = [...definitions.entries()]
  .filter(([className]) => !isUsed(className))
  .map(([className, locations]) => `${className}: ${[...locations].join(', ')}`)
  .sort()
assert.deepEqual(unusedClasses, [], `unused frontend CSS classes remain:\n${unusedClasses.join('\n')}`)

const layeredClasses = [...definitions.entries()]
  .filter(([, locations]) => locations.size > 1)
  .map(([className, locations]) => ({ className, stylesheets: [...locations].sort() }))
  .sort((a, b) => a.className.localeCompare(b.className))

console.log(JSON.stringify({
  ok: true,
  cssFiles: cssFiles.length,
  definedClasses: definitions.size,
  layeredClasses,
  dynamicPrefixes: [...dynamicPrefixes].sort(),
}, null, 2))
