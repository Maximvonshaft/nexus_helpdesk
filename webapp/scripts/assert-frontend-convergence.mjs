import assert from 'node:assert/strict'
import { basename, join, relative, resolve } from 'node:path'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import ts from 'typescript'

const webappRoot = resolve(import.meta.dirname, '..')
const repoRoot = resolve(webappRoot, '..')
const srcRoot = join(webappRoot, 'src')
const uiRoot = join(srcRoot, 'components', 'ui')
const apiClientPath = join(srcRoot, 'lib', 'apiClient.ts')
const tokenPath = join(srcRoot, 'styles', 'tokens.css')

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

function lineCount(path) {
  return read(path).split(/\r?\n/).length
}

const retiredPaths = [
  [join(repoRoot, 'frontend'), 'legacy frontend/ must be physically deleted'],
  [join(repoRoot, '.github', 'workflows', 'generate-radix-lockfile.yml'), 'obsolete Radix dependency generator must be deleted'],
  [join(srcRoot, 'features', 'support-console'), 'duplicate Support Console must be deleted'],
  [join(srcRoot, 'shared', 'ui'), 'duplicate shared/ui authority must be deleted'],
  [join(srcRoot, 'shared', 'api'), 'obsolete shared/api migration authority must be deleted'],
  [join(srcRoot, 'lib', 'api.ts'), 'duplicate frontend API client must be deleted'],
  [join(srcRoot, 'lib', 'apiErrorMap.ts'), 'orphaned legacy API error map must be deleted'],
  [join(srcRoot, 'lib', 'uxCopy.ts'), 'orphaned legacy copy registry must be deleted'],
  [join(srcRoot, 'lib', 'webchatRealtime.ts'), 'orphaned WebChat realtime client must be deleted'],
  [join(srcRoot, 'lib', 'supportStatus.ts'), 'retired Support Console status presenter must be deleted'],
  [join(srcRoot, 'lib', 'webchatVoiceTypes.ts'), 'orphaned voice AI frontend types must be deleted'],
  [join(srcRoot, 'lib', 'outboundChannels.ts'), 'orphaned outbound-channel helper must be deleted'],
]
for (const [path, message] of retiredPaths) assert.equal(existsSync(path), false, message)

const routeNames = readdirSync(join(srcRoot, 'routes')).filter((name) => name.endsWith('.tsx')).sort()
assert.deepEqual(routeNames, ['channels.tsx', 'index.tsx', 'knowledge.tsx', 'login.tsx', 'root.tsx', 'system.tsx', 'webchat.tsx', 'workspace.tsx'])

const webchatRoute = read(join(srcRoot, 'routes', 'webchat.tsx'))
assert.match(webchatRoute, /redirect\(\{ to: getSupportToken\(\) \? '\/workspace' : '\/login'/)
assert.doesNotMatch(webchatRoute, /support-console|SupportConsole|lazy\(/)

const sourceFiles = walk(srcRoot).filter((path) => /\.(?:ts|tsx|css)$/.test(path)).sort()
const codeFiles = sourceFiles.filter((path) => /\.(?:ts|tsx)$/.test(path))
const cssFiles = sourceFiles.filter((path) => path.endsWith('.css'))

for (const path of codeFiles) {
  const source = read(path)
  const label = relativePath(path)
  assert.doesNotMatch(
    source,
    /@\/shared\/(?:ui|api)|features\/support-console|@\/lib\/api(?:['"]|$)|apiErrorMap|uxCopy|webchatRealtime|webchatVoiceTypes|supportStatus|outboundChannels/,
    `obsolete frontend import or reference in ${label}`,
  )

  if (path !== apiClientPath) {
    assert.doesNotMatch(source, /\bfetch\s*\(/, `raw fetch outside apiClient authority: ${label}`)
    assert.doesNotMatch(source, /\bnew\s+AbortController\s*\(/, `duplicate timeout boundary outside apiClient: ${label}`)
    assert.doesNotMatch(source, /helpdesk-webapp-token/, `duplicate auth-token ownership outside apiClient: ${label}`)
    assert.doesNotMatch(source, /headers\.set\(\s*['"]Authorization['"]|['"]Authorization['"]\s*:/, `duplicate Authorization handling outside apiClient: ${label}`)
  }
}

const apiAuthorityFiles = codeFiles.filter((path) => /export\s+async\s+function\s+apiRequest\b/.test(read(path)))
assert.deepEqual(apiAuthorityFiles.map(relativePath), ['src/lib/apiClient.ts'])
assert.match(read(apiClientPath), /export async function apiRequest/)
assert.deepEqual(
  codeFiles.filter((path) => /function\s+normalizeApiBaseUrl\b/.test(read(path))).map(relativePath),
  ['src/lib/apiClient.ts'],
  'API base normalization must have one owner',
)

const uiFiles = walk(uiRoot).filter((path) => path.endsWith('.tsx')).sort()
for (const authorityPath of uiFiles) {
  const name = basename(authorityPath)
  const matches = codeFiles.filter((path) => path.endsWith('.tsx') && basename(path) === name).map(relativePath)
  assert.deepEqual(matches, [relativePath(authorityPath)], `duplicate shared UI authority for ${name}`)
}

const pagePath = join(srcRoot, 'features', 'operator-workspace', 'OperatorWorkspacePage.tsx')
assert.ok(lineCount(pagePath) <= 420, `OperatorWorkspacePage exceeds 420 lines: ${lineCount(pagePath)}`)
for (const path of walk(join(srcRoot, 'features', 'operator-workspace', 'components')).filter((value) => value.endsWith('.tsx'))) {
  assert.ok(lineCount(path) <= 420, `${relativePath(path)} exceeds 420 lines: ${lineCount(path)}`)
}
for (const path of [
  join(srcRoot, 'features', 'operator-workspace', 'operator-workspace.css'),
  join(srcRoot, 'styles', 'service-shell.css'),
]) {
  assert.ok(lineCount(path) <= 800, `${relativePath(path)} exceeds 800 lines: ${lineCount(path)}`)
}

function collectJsxVisibleStrings(sourceFile) {
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

function collectStringLiterals(sourceFile) {
  const values = []
  function visit(node) {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) values.push(node.text)
    else if (ts.isTemplateExpression(node)) {
      values.push(node.head.text)
      for (const span of node.templateSpans) values.push(span.literal.text)
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  return values
}

const forbiddenVisibleTerms = /\b(?:AI|Artificial Intelligence|Runtime|Provider|RAG|Prompt|Model|Agent)\b/i
const visibleFindings = []
for (const path of codeFiles.filter((value) => value.endsWith('.tsx'))) {
  const sourceFile = ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  for (const value of collectJsxVisibleStrings(sourceFile)) {
    if (forbiddenVisibleTerms.test(value)) visibleFindings.push(`${relativePath(path)}: ${value.trim()}`)
  }
}
for (const path of codeFiles.filter((value) => value.endsWith('.ts') && /(?:Presentation|Status|Copy|Map|Labels|Messages)\.ts$/i.test(basename(value)))) {
  const sourceFile = ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  for (const value of collectStringLiterals(sourceFile)) {
    if (forbiddenVisibleTerms.test(value)) visibleFindings.push(`${relativePath(path)}: ${value.trim()}`)
  }
}
assert.deepEqual(visibleFindings, [], `operator-visible internal terminology remains:\n${visibleFindings.join('\n')}`)

for (const path of cssFiles) {
  if (path === tokenPath) continue
  const source = read(path)
  const label = relativePath(path)
  assert.doesNotMatch(source, /#[0-9a-f]{3,8}\b/i, `raw hex color outside token authority: ${label}`)
  assert.doesNotMatch(source, /\b(?:rgb|rgba|hsl|hsla)\s*\(/i, `raw functional color outside token authority: ${label}`)
}
for (const path of codeFiles.filter((value) => value.endsWith('.tsx'))) {
  assert.doesNotMatch(
    read(path),
    /(?:color|background(?:Color)?|borderColor)\s*:\s*['"`]#[0-9a-f]{3,8}\b/i,
    `raw inline color outside token authority: ${relativePath(path)}`,
  )
}
assert.match(read(tokenPath), /--nd-color-/)

const packageJson = JSON.parse(read(join(webappRoot, 'package.json')))
const packageLock = JSON.parse(read(join(webappRoot, 'package-lock.json')))
const dependencies = packageJson.dependencies ?? {}
const retiredPackages = [
  '@radix-ui/react-dropdown-menu',
  '@radix-ui/react-popover',
  '@radix-ui/react-select',
  '@radix-ui/react-tabs',
  '@radix-ui/react-tooltip',
  'livekit-client',
]
for (const packageName of retiredPackages) {
  assert.equal(packageName in dependencies, false, `retired direct dependency remains: ${packageName}`)
  assert.equal(packageName in (packageLock.packages?.['']?.dependencies ?? {}), false, `retired lockfile dependency remains: ${packageName}`)
  assert.equal(Boolean(packageLock.packages?.[`node_modules/${packageName}`]), false, `retired package node remains in lockfile: ${packageName}`)
}
assert.deepEqual(Object.keys(dependencies).filter((name) => name.startsWith('@radix-ui/')).sort(), ['@radix-ui/react-dialog'])
assert.equal(Object.keys(packageLock.packages ?? {}).some((name) => name.startsWith('node_modules/@livekit/')), false, 'LiveKit transitive packages remain in lockfile')
assert.doesNotMatch(read(join(webappRoot, 'vite.config.ts')), /livekit|vendor-livekit/i)

console.log(JSON.stringify({
  ok: true,
  routes: routeNames,
  sourceFiles: sourceFiles.length,
  uiAuthorities: uiFiles.length,
  apiAuthorities: apiAuthorityFiles.map(relativePath),
  visibleTerminologyFindings: visibleFindings.length,
  retiredDependencies: retiredPackages,
  workspacePageLines: lineCount(pagePath),
}, null, 2))
