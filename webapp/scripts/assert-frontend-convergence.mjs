import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import ts from 'typescript'

const webappRoot = resolve(import.meta.dirname, '..')
const repoRoot = resolve(webappRoot, '..')
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

function lineCount(path) {
  return read(path).split(/\r?\n/).length
}

assert.equal(existsSync(join(repoRoot, 'frontend')), false, 'legacy frontend/ must be physically deleted')
assert.equal(existsSync(join(srcRoot, 'features', 'support-console')), false, 'duplicate Support Console must be deleted')
assert.equal(existsSync(join(srcRoot, 'shared', 'ui')), false, 'duplicate shared/ui authority must be deleted')

const routeNames = readdirSync(join(srcRoot, 'routes')).filter((name) => name.endsWith('.tsx')).sort()
assert.deepEqual(routeNames, ['channels.tsx', 'index.tsx', 'knowledge.tsx', 'login.tsx', 'root.tsx', 'system.tsx', 'webchat.tsx', 'workspace.tsx'])

const webchatRoute = read(join(srcRoot, 'routes', 'webchat.tsx'))
assert.match(webchatRoute, /redirect\(\{ to: getSupportToken\(\) \? '\/workspace' : '\/login'/)
assert.doesNotMatch(webchatRoute, /support-console|SupportConsole|lazy\(/)

const sourceFiles = walk(srcRoot).filter((path) => /\.(?:ts|tsx|css)$/.test(path))
for (const path of sourceFiles) {
  const source = read(path)
  assert.doesNotMatch(source, /@\/shared\/ui|features\/support-console/, `obsolete UI import in ${relative(webappRoot, path)}`)
}

const buttonFiles = sourceFiles.filter((path) => path.endsWith('Button.tsx'))
assert.deepEqual(buttonFiles.map((path) => relative(webappRoot, path)), ['src/components/ui/Button.tsx'])
const badgeFiles = sourceFiles.filter((path) => path.endsWith('Badge.tsx'))
assert.deepEqual(badgeFiles.map((path) => relative(webappRoot, path)), ['src/components/ui/Badge.tsx'])

const pagePath = join(srcRoot, 'features', 'operator-workspace', 'OperatorWorkspacePage.tsx')
assert.ok(lineCount(pagePath) <= 420, `OperatorWorkspacePage exceeds 420 lines: ${lineCount(pagePath)}`)
for (const path of walk(join(srcRoot, 'features', 'operator-workspace', 'components')).filter((value) => value.endsWith('.tsx'))) {
  assert.ok(lineCount(path) <= 420, `${relative(webappRoot, path)} exceeds 420 lines: ${lineCount(path)}`)
}
for (const path of [
  join(srcRoot, 'features', 'operator-workspace', 'operator-workspace.css'),
  join(srcRoot, 'styles', 'service-shell.css'),
]) {
  assert.ok(lineCount(path) <= 800, `${relative(webappRoot, path)} exceeds 800 lines: ${lineCount(path)}`)
}

const apiClient = read(join(srcRoot, 'lib', 'apiClient.ts'))
const supportApi = read(join(srcRoot, 'lib', 'supportApi.ts'))
const workspaceApi = read(join(srcRoot, 'lib', 'operatorWorkspaceApi.ts'))
assert.match(apiClient, /export async function apiRequest/)
assert.doesNotMatch(supportApi, /\bfetch\(/)
assert.doesNotMatch(workspaceApi, /\bfetch\(|new AbortController/)
assert.match(supportApi, /apiRequest/)
assert.match(workspaceApi, /apiRequest/)

const visibleRoots = [
  join(srcRoot, 'routes'),
  join(srcRoot, 'features'),
  join(srcRoot, 'components', 'layout'),
]
const forbiddenVisibleTerms = /\b(?:AI|Artificial Intelligence|Runtime|Provider|RAG|Prompt|Model|Agent)\b/i
const findings = []

function collectVisibleStrings(sourceFile) {
  const values = []
  function collectWithinJsx(node) {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) values.push(node.text)
    else if (ts.isTemplateExpression(node)) {
      values.push(node.head.text)
      for (const span of node.templateSpans) values.push(span.literal.text)
    }
    ts.forEachChild(node, collectWithinJsx)
  }
  function visit(node) {
    if (ts.isJsxText(node)) values.push(node.getText(sourceFile))
    if (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer)) values.push(node.initializer.text)
    if (ts.isJsxExpression(node) && node.expression) collectWithinJsx(node.expression)
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  return values
}

for (const root of visibleRoots) {
  for (const path of walk(root).filter((value) => value.endsWith('.tsx'))) {
    const source = read(path)
    const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    for (const value of collectVisibleStrings(sourceFile)) {
      if (forbiddenVisibleTerms.test(value)) findings.push(`${relative(webappRoot, path)}: ${value.trim()}`)
    }
  }
}
assert.deepEqual(findings, [], `operator-visible technical/AI terminology remains:\n${findings.join('\n')}`)

const tokenPath = join(srcRoot, 'styles', 'tokens.css')
for (const path of [
  join(srcRoot, 'styles.css'),
  join(srcRoot, 'styles', 'components.css'),
  join(srcRoot, 'styles', 'auth.css'),
  join(srcRoot, 'styles', 'service-shell.css'),
  join(srcRoot, 'features', 'operator-workspace', 'operator-workspace.css'),
]) {
  const source = read(path)
  assert.doesNotMatch(source, /#[0-9a-f]{3,8}\b/i, `raw color found outside token authority: ${relative(webappRoot, path)}`)
}
assert.match(read(tokenPath), /--nd-color-/)

console.log(JSON.stringify({
  ok: true,
  routes: routeNames,
  sourceFiles: sourceFiles.length,
  visibleTerminologyFindings: findings.length,
  workspacePageLines: lineCount(pagePath),
}, null, 2))
