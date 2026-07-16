#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const webappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(webappRoot, '..')
const srcRoot = path.join(webappRoot, 'src')
const entrypoint = path.join(srcRoot, 'main.tsx')
const muiAuthorityPath = path.join(webappRoot, 'design', 'mui-visual-authority.v1.json')

const SOURCE_EXTENSIONS = ['.ts', '.tsx', '.css']
const IMPORT_RE = /(?:import|export)\s+(?:[^'"()]*?\s+from\s+)?["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)/g
const PRIMITIVE_EXPORT_RE = /export\s+(?:const|function|class)\s+(AppShell|AppNavigation|Button|ButtonLink|Badge|Card|Field|Input|Select|Textarea|ConfirmDialog|EmptyState|ErrorSummary|TechnicalDetails|PageHeader|StatusIndicator|Count)\b/g
const LEGACY_PALETTE_RE = /--(?:bg|panel|panel-soft|line|line-strong|text|muted|brand|brand-2|success|warning|danger|shadow|radius)\s*:/g
const LEGACY_SELECTOR_RE = /(^|[,\s{])\.(?:button|badge|card)(?=[\s,{.:#\[])/gm
const FORBIDDEN_PARALLEL_PATH_RE = /(?:^|\/)(?:new-ui|ui-v2|design-system-v2|components-v2|workspace-v2|new-workspace)(?:\/|$)|(?:^|\/)[^/]*(?:V2|Redesign)\.(?:ts|tsx|css)$/i
const FORBIDDEN_ROUTE_RE = /["']\/(?:workspace-v2|new-workspace|ui-v2)(?:[/?#"']|$)/i
const FORBIDDEN_UI_PACKAGES = new Set([
  '@chakra-ui/react',
  '@mantine/core',
  '@mantine/hooks',
  'antd',
  'bootstrap',
  'react-bootstrap',
  'semantic-ui-react',
  'primereact',
  'tailwindcss',
  'daisyui',
  'flowbite',
  'flowbite-react',
  'shadcn',
])
const APPROVED_MUI_DIRECT_PACKAGES = new Set([
  '@mui/material',
  '@mui/icons-material',
])
const APPROVED_EMOTION_DIRECT_PACKAGES = new Set([
  '@emotion/react',
  '@emotion/styled',
])

const forbiddenPaths = [
  path.join(repositoryRoot, 'frontend'),
  path.join(srcRoot, 'features', 'support-console'),
  path.join(srcRoot, 'shared', 'ui'),
  path.join(srcRoot, 'shared', 'api'),
  path.join(srcRoot, 'lib', 'api.ts'),
  path.join(srcRoot, 'lib', 'webchatRealtime.ts'),
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

function externalImports(files) {
  const imports = new Set()
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    for (const match of content.matchAll(IMPORT_RE)) {
      const specifier = match[1] ?? match[2]
      if (!specifier || specifier.startsWith('.') || specifier.startsWith('@/')) continue
      const packageName = specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0]
      imports.add(packageName)
    }
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

function readMuiAuthority(failures) {
  if (!fs.existsSync(muiAuthorityPath)) {
    failures.push('MUI visual authority contract is missing: webapp/design/mui-visual-authority.v1.json')
    return null
  }
  try {
    const authority = JSON.parse(fs.readFileSync(muiAuthorityPath, 'utf8'))
    if (authority.schema !== 'nexus.mui-visual-authority.v1') failures.push(`unexpected MUI authority schema: ${authority.schema}`)
    if (authority.decision?.selected_package !== '@mui/material') failures.push('MUI authority must select @mui/material')
    if (authority.decision?.selected_version !== '9.2.0') failures.push(`MUI version must be exactly 9.2.0: ${authority.decision?.selected_version}`)
    return authority
  } catch (error) {
    failures.push(`MUI visual authority contract is invalid JSON: ${error instanceof Error ? error.message : String(error)}`)
    return null
  }
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

function assertActionsRetired(failures) {
  const workflowDir = path.join(repositoryRoot, '.github', 'workflows')
  if (!fs.existsSync(workflowDir)) return
  const workflowFiles = walk(workflowDir).map(relative)
  failures.push(`GitHub Actions are retired; .github/workflows must be absent: ${workflowFiles.join(', ') || 'empty directory exists'}`)
}

function assertNoParallelImplementation(files, failures) {
  const forbiddenNames = files.map(relative).filter((file) => FORBIDDEN_PARALLEL_PATH_RE.test(file))
  if (forbiddenNames.length) failures.push(`parallel UI implementation path is forbidden: ${forbiddenNames.join(', ')}`)

  const forbiddenRoutes = []
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    if (FORBIDDEN_ROUTE_RE.test(content)) forbiddenRoutes.push(relative(file))
  }
  if (forbiddenRoutes.length) failures.push(`parallel UI route is forbidden: ${forbiddenRoutes.join(', ')}`)
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

function assertCssAuthority(files, failures) {
  for (const file of files.filter((candidate) => candidate.endsWith('.css'))) {
    const content = fs.readFileSync(file, 'utf8')
    const fileName = relative(file)
    if (fileName !== 'webapp/src/styles/tokens.css' && LEGACY_PALETTE_RE.test(content)) {
      failures.push(`second palette authority: ${fileName}`)
    }
    LEGACY_PALETTE_RE.lastIndex = 0
    if (LEGACY_SELECTOR_RE.test(content)) failures.push(`legacy primitive selector outside nd-* authority: ${fileName}`)
    LEGACY_SELECTOR_RE.lastIndex = 0
    if (/transition\s*:\s*all\b/i.test(content)) failures.push(`transition: all is forbidden: ${fileName}`)
  }
}

function assertSelectedVisualStack(manifest, authority, failures) {
  const allDependencies = {
    ...(manifest.dependencies ?? {}),
    ...(manifest.devDependencies ?? {}),
  }

  for (const dependency of Object.keys(allDependencies)) {
    if (FORBIDDEN_UI_PACKAGES.has(dependency) || dependency.startsWith('@tailwindcss/')) {
      failures.push(`parallel UI framework dependency is forbidden; MUI is selected: ${dependency}`)
    }
    if (dependency.startsWith('@mui/') && !APPROVED_MUI_DIRECT_PACKAGES.has(dependency)) {
      failures.push(`unapproved direct MUI package: ${dependency}`)
    }
    if (dependency.startsWith('@emotion/') && !APPROVED_EMOTION_DIRECT_PACKAGES.has(dependency)) {
      failures.push(`unapproved direct Emotion package: ${dependency}`)
    }
  }

  const muiInstalled = Object.hasOwn(manifest.dependencies ?? {}, '@mui/material')
  const status = authority?.decision?.status
  if (!muiInstalled) {
    if (status !== 'authorized_not_installed') failures.push(`MUI authority status ${status} requires @mui/material to be installed`)
    return
  }

  const expected = authority.runtime_packages
  for (const [dependency, version] of Object.entries(expected)) {
    if ((manifest.dependencies ?? {})[dependency] !== version) {
      failures.push(`selected visual dependency must be pinned exactly: ${dependency}@${version}`)
    }
  }
  if ((manifest.overrides ?? {})['react-is'] !== authority.react_compatibility.react_is_override) {
    failures.push(`React 18 requires package.json overrides.react-is=${authority.react_compatibility.react_is_override}`)
  }
}

function assertRuntimeDependencies(files, authority, failures) {
  const manifest = JSON.parse(fs.readFileSync(path.join(webappRoot, 'package.json'), 'utf8'))
  const consumed = externalImports(files)
  const configFiles = walk(webappRoot).filter((file) => /(?:vite\.config|playwright\.config|eslint\.config|\.mjs$)/.test(file))
  for (const file of configFiles) {
    const content = fs.readFileSync(file, 'utf8')
    for (const match of content.matchAll(IMPORT_RE)) {
      const specifier = match[1] ?? match[2]
      if (!specifier || specifier.startsWith('.') || specifier.startsWith('@/') || specifier.startsWith('node:')) continue
      consumed.add(specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0])
    }
  }
  for (const dependency of Object.keys(manifest.dependencies ?? {})) {
    if (!consumed.has(dependency)) failures.push(`unused runtime dependency: ${dependency}`)
  }
  assertSelectedVisualStack(manifest, authority, failures)
}

const failures = []
for (const forbidden of forbiddenPaths) {
  if (fs.existsSync(forbidden)) failures.push(`retired path exists: ${relative(forbidden)}`)
}
assertActionsRetired(failures)
const muiAuthority = readMuiAuthority(failures)

const files = sourceFiles()
const reachable = reachableFiles()
const unreachable = files.filter((file) => !reachable.has(file)).map(relative)
if (unreachable.length) failures.push(`unreachable production files: ${unreachable.join(', ')}`)

const duplicatePrimitives = duplicatePrimitiveAuthorities(files)
if (duplicatePrimitives.length) failures.push(`duplicate UI authorities: ${duplicatePrimitives.join(' | ')}`)

assertNoParallelImplementation(files, failures)
assertCanonicalNavigation(files, failures)
assertTransportAuthority(files, failures)
assertCssAuthority(files, failures)
assertRuntimeDependencies(files, muiAuthority, failures)

if (failures.length) {
  console.error(JSON.stringify({ ok: false, failures }, null, 2))
  process.exit(1)
}

console.log(JSON.stringify({
  ok: true,
  production_files: files.length,
  reachable_files: reachable.size,
  canonical_entrypoint: relative(entrypoint),
  github_actions: 'retired',
  current_ui_authority: 'webapp/src/components/ui during unmerged migration',
  target_ui_authority: '@mui/material@9.2.0',
  target_theme_authority: 'webapp/src/theme/nexusTheme.ts',
  migration_status: muiAuthority?.decision?.status,
}, null, 2))
