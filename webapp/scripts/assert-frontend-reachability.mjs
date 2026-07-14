import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, extname, join, relative, resolve } from 'node:path'
import ts from 'typescript'

const webappRoot = resolve(import.meta.dirname, '..')
const srcRoot = join(webappRoot, 'src')
const entryPoints = [join(srcRoot, 'main.tsx')]
const sourceExtensions = new Set(['.ts', '.tsx', '.css'])
const resolvableExtensions = ['', '.ts', '.tsx', '.css']

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

function moduleCandidates(base) {
  const candidates = resolvableExtensions.map((extension) => `${base}${extension}`)
  candidates.push(join(base, 'index.ts'), join(base, 'index.tsx'), join(base, 'index.css'))
  return candidates
}

function resolveLocalModule(fromPath, specifier) {
  let base
  if (specifier.startsWith('@/')) base = join(srcRoot, specifier.slice(2))
  else if (specifier.startsWith('.')) base = resolve(dirname(fromPath), specifier)
  else return null

  for (const candidate of moduleCandidates(base)) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate
  }
  return undefined
}

function packageName(specifier) {
  if (!specifier || specifier.startsWith('.') || specifier.startsWith('@/') || specifier.startsWith('/')) return null
  if (specifier.startsWith('@')) return specifier.split('/').slice(0, 2).join('/')
  return specifier.split('/')[0]
}

function collectCodeSpecifiers(path) {
  const source = read(path)
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, kind)
  const specifiers = []

  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      specifiers.push(node.moduleSpecifier.text)
    }
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length === 1 &&
      ts.isStringLiteral(node.arguments[0])
    ) {
      specifiers.push(node.arguments[0].text)
    }
    ts.forEachChild(node, visit)
  }

  visit(sourceFile)
  return specifiers
}

function collectCssSpecifiers(path) {
  const values = []
  const pattern = /@import\s+(?:url\()?\s*['"]([^'"]+)['"]\s*\)?/g
  for (const match of read(path).matchAll(pattern)) values.push(match[1])
  return values
}

function collectSpecifiers(path) {
  return path.endsWith('.css') ? collectCssSpecifiers(path) : collectCodeSpecifiers(path)
}

const productionFiles = walk(srcRoot)
  .filter((path) => sourceExtensions.has(extname(path)))
  .sort()
const productionFileSet = new Set(productionFiles)
const graph = new Map()
const unresolvedLocalImports = []
const importedPackages = new Set()

for (const path of productionFiles) {
  const dependencies = []
  for (const specifier of collectSpecifiers(path)) {
    const dependency = resolveLocalModule(path, specifier)
    if (dependency === undefined) {
      unresolvedLocalImports.push(`${relativePath(path)} -> ${specifier}`)
      continue
    }
    if (dependency) dependencies.push(dependency)
    const externalPackage = packageName(specifier)
    if (externalPackage) importedPackages.add(externalPackage)
  }
  graph.set(path, dependencies)
}

assert.deepEqual(unresolvedLocalImports, [], `unresolved local frontend imports:\n${unresolvedLocalImports.join('\n')}`)
for (const entryPoint of entryPoints) assert.ok(productionFileSet.has(entryPoint), `frontend entry point is missing: ${relativePath(entryPoint)}`)

const reachable = new Set()
const pending = [...entryPoints]
while (pending.length) {
  const path = pending.pop()
  if (!path || reachable.has(path)) continue
  reachable.add(path)
  for (const dependency of graph.get(path) ?? []) pending.push(dependency)
}

const unreachable = productionFiles
  .filter((path) => !path.endsWith('.d.ts'))
  .filter((path) => !reachable.has(path))
  .map(relativePath)
assert.deepEqual(unreachable, [], `unreachable production frontend modules remain:\n${unreachable.join('\n')}`)

const packageJson = JSON.parse(read(join(webappRoot, 'package.json')))
const directDependencies = Object.keys(packageJson.dependencies ?? {}).sort()
const unusedDirectDependencies = directDependencies.filter((name) => !importedPackages.has(name))
assert.deepEqual(unusedDirectDependencies, [], `unused direct frontend dependencies remain:\n${unusedDirectDependencies.join('\n')}`)

const declaredPackages = new Set([
  ...Object.keys(packageJson.dependencies ?? {}),
  ...Object.keys(packageJson.devDependencies ?? {}),
])
const undeclaredPackages = [...importedPackages]
  .filter((name) => !declaredPackages.has(name))
  .sort()
assert.deepEqual(undeclaredPackages, [], `frontend imports undeclared packages:\n${undeclaredPackages.join('\n')}`)

console.log(JSON.stringify({
  ok: true,
  entryPoints: entryPoints.map(relativePath),
  productionFiles: productionFiles.length,
  reachableFiles: reachable.size,
  importedPackages: [...importedPackages].sort(),
  directDependencies,
}, null, 2))
