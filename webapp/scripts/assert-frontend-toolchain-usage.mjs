import assert from 'node:assert/strict'
import { builtinModules } from 'node:module'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { extname, join, relative, resolve } from 'node:path'
import ts from 'typescript'

const webappRoot = resolve(import.meta.dirname, '..')
const ignoredDirectories = new Set(['node_modules', 'dist', 'playwright-report', 'test-results'])
const parseableExtensions = new Set(['.js', '.mjs', '.ts', '.tsx'])
const builtinNames = new Set([...builtinModules, ...builtinModules.map((name) => `node:${name}`)])

function walk(root) {
  if (!existsSync(root)) return []
  const files = []
  for (const name of readdirSync(root)) {
    if (ignoredDirectories.has(name)) continue
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

function packageName(specifier) {
  if (!specifier || specifier.startsWith('.') || specifier.startsWith('/') || specifier.startsWith('@/') || builtinNames.has(specifier)) return null
  if (specifier.startsWith('@')) return specifier.split('/').slice(0, 2).join('/')
  return specifier.split('/')[0]
}

function collectCodePackages(path) {
  const source = read(path)
  const extension = extname(path)
  const kind = extension === '.tsx' ? ts.ScriptKind.TSX : extension === '.ts' ? ts.ScriptKind.TS : ts.ScriptKind.JS
  const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, kind)
  const packages = new Set()

  function addSpecifier(value) {
    const name = packageName(value)
    if (name) packages.add(name)
  }

  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      addSpecifier(node.moduleSpecifier.text)
    }
    if (ts.isCallExpression(node) && node.arguments.length > 0 && ts.isStringLiteral(node.arguments[0])) {
      if (node.expression.kind === ts.SyntaxKind.ImportKeyword) addSpecifier(node.arguments[0].text)
      if (ts.isIdentifier(node.expression) && node.expression.text === 'require') addSpecifier(node.arguments[0].text)
    }
    ts.forEachChild(node, visit)
  }

  visit(sourceFile)
  return packages
}

function collectCssPackages(path) {
  const packages = new Set()
  for (const match of read(path).matchAll(/@import\s+(?:url\()?\s*['"]([^'"]+)['"]\s*\)?/g)) {
    const name = packageName(match[1])
    if (name) packages.add(name)
  }
  return packages
}

const packageJson = JSON.parse(read(join(webappRoot, 'package.json')))
const declared = new Set([
  ...Object.keys(packageJson.dependencies ?? {}),
  ...Object.keys(packageJson.devDependencies ?? {}),
])
const used = new Set()
const usageLocations = new Map()

function record(name, location) {
  used.add(name)
  const locations = usageLocations.get(name) ?? new Set()
  locations.add(location)
  usageLocations.set(name, locations)
}

for (const path of walk(webappRoot)) {
  const extension = extname(path)
  const packages = parseableExtensions.has(extension)
    ? collectCodePackages(path)
    : extension === '.css'
      ? collectCssPackages(path)
      : new Set()
  for (const name of packages) record(name, relativePath(path))
}

const binaryOwners = new Map([
  ['eslint', 'eslint'],
  ['playwright', '@playwright/test'],
  ['tsc', 'typescript'],
  ['vite', 'vite'],
])
for (const [scriptName, command] of Object.entries(packageJson.scripts ?? {})) {
  const tokens = String(command).split(/[^A-Za-z0-9@/_-]+/).filter(Boolean)
  for (const token of tokens) {
    const owner = binaryOwners.get(token)
    if (owner) record(owner, `package.json#scripts.${scriptName}`)
  }
}

if (declared.has('@types/react') && declared.has('react')) record('@types/react', 'TypeScript JSX declarations for react')
if (declared.has('@types/react-dom') && declared.has('react-dom')) record('@types/react-dom', 'TypeScript declarations for react-dom')

const undeclared = [...used].filter((name) => !declared.has(name)).sort()
assert.deepEqual(undeclared, [], `frontend toolchain imports undeclared packages:\n${undeclared.join('\n')}`)

const unused = [...declared].filter((name) => !used.has(name)).sort()
assert.deepEqual(unused, [], `unused frontend manifest dependencies remain:\n${unused.join('\n')}`)

console.log(JSON.stringify({
  ok: true,
  declaredPackages: [...declared].sort(),
  usage: Object.fromEntries([...usageLocations.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, locations]) => [name, [...locations].sort()])),
}, null, 2))
