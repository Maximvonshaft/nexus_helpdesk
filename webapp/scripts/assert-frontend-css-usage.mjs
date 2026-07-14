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
const usageCorpus = [read(join(webappRoot, 'index.html')), ...codeFiles.map(read)].join('\n')
const dynamicPrefixes = new Set()
for (const match of usageCorpus.matchAll(/([A-Za-z_][\w-]*--|[A-Za-z_][\w-]*-)\$\{/g)) dynamicPrefixes.add(match[1])

const definitions = new Map()
for (const path of cssFiles) {
  const classes = new Set()
  for (const match of read(path).matchAll(/\.([A-Za-z_][\w-]*)/g)) classes.add(match[1])
  for (const className of classes) {
    const locations = definitions.get(className) ?? []
    locations.push(relativePath(path))
    definitions.set(className, locations)
  }
}

const duplicateDefinitions = [...definitions.entries()]
  .filter(([, locations]) => new Set(locations).size > 1)
  .map(([className, locations]) => `${className}: ${[...new Set(locations)].join(', ')}`)
  .sort()
assert.deepEqual(duplicateDefinitions, [], `CSS classes have multiple source authorities:\n${duplicateDefinitions.join('\n')}`)

function isUsed(className) {
  const exact = new RegExp(`(^|[^A-Za-z0-9_-])${className.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^A-Za-z0-9_-]|$)`)
  if (exact.test(usageCorpus)) return true
  return [...dynamicPrefixes].some((prefix) => className.startsWith(prefix))
}

const unusedClasses = [...definitions.entries()]
  .filter(([className]) => !isUsed(className))
  .map(([className, locations]) => `${className}: ${locations.join(', ')}`)
  .sort()
assert.deepEqual(unusedClasses, [], `unused frontend CSS classes remain:\n${unusedClasses.join('\n')}`)

console.log(JSON.stringify({
  ok: true,
  cssFiles: cssFiles.length,
  definedClasses: definitions.size,
  dynamicPrefixes: [...dynamicPrefixes].sort(),
}, null, 2))
