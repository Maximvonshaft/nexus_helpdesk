#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const webappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(webappRoot, '..')
const srcRoot = path.join(webappRoot, 'src')
const entrypoint = path.join(srcRoot, 'main.tsx')
const packagePath = path.join(webappRoot, 'package.json')
const lockPath = path.join(webappRoot, 'package-lock.json')
const muiAuthorityPath = path.join(webappRoot, 'design', 'mui-visual-authority.v1.json')
const presentationPath = path.join(srcRoot, 'app', 'OperatorPresentation.tsx')
const themePath = path.join(srcRoot, 'theme', 'nexusTheme.ts')
const themeProviderPath = path.join(srcRoot, 'theme', 'NexusThemeProvider.tsx')
const workspacePath = path.join(srcRoot, 'features', 'operator-workspace', 'OperatorWorkspacePage.tsx')
const knowledgePath = path.join(srcRoot, 'features', 'knowledge', 'KnowledgePage.tsx')
const knowledgeRoutePath = path.join(srcRoot, 'routes', 'knowledge.tsx')

const SOURCE_EXTENSIONS = ['.ts', '.tsx', '.css']
const IMPORT_RE = /(?:import|export)\s+(?:[^'"()]*?\s+from\s+)?["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)/g
const RAW_COLOR_RE = /#[0-9a-f]{3,8}\b|rgba?\(\s*\d|hsla?\(\s*\d/gi
const FORBIDDEN_PARALLEL_PATH_RE = /(?:^|\/)(?:new-ui|ui-v2|design-system-v2|components-v2|workspace-v2|new-workspace)(?:\/|$)|(?:^|\/)[^/]*(?:V2|Redesign)\.(?:ts|tsx|css)$/i
const FORBIDDEN_ROUTE_RE = /["']\/(?:workspace-v2|new-workspace|ui-v2)(?:[/?#"']|$)/i
const GENERIC_EXPORT_RE = /export\s+(?:const|function|class)\s+(AppShell|AppNavigation|Button|ButtonLink|Badge|Card|Field|Input|Select|Textarea|ConfirmDialog|EmptyState|ErrorSummary|TechnicalDetails|PageHeader|StatusIndicator|Count)\b/g

const ALLOWED_SOURCE_CSS = new Set(['webapp/src/styles.css', 'webapp/src/a11y.css'])
const APPROVED_MUI_DIRECT_PACKAGES = new Set(['@mui/material', '@mui/icons-material'])
const APPROVED_EMOTION_DIRECT_PACKAGES = new Set(['@emotion/react', '@emotion/styled'])
const REQUIRED_VISUAL_SUPPORT_PACKAGES = new Set(['@emotion/react', '@emotion/styled', 'react-is'])
const FORBIDDEN_UI_PACKAGES = new Set([
  '@chakra-ui/react', '@mantine/core', '@mantine/hooks', 'antd', 'bootstrap',
  'react-bootstrap', 'semantic-ui-react', 'primereact', 'tailwindcss',
  'daisyui', 'flowbite', 'flowbite-react', 'shadcn', '@radix-ui/react-dialog',
])

const forbiddenPaths = [
  path.join(repositoryRoot, 'frontend'),
  path.join(srcRoot, 'features', 'support-console'),
  path.join(srcRoot, 'shared', 'ui'),
  path.join(srcRoot, 'shared', 'api'),
  path.join(srcRoot, 'lib', 'api.ts'),
  path.join(srcRoot, 'lib', 'webchatRealtime.ts'),
  path.join(srcRoot, 'components', 'ui'),
  path.join(srcRoot, 'styles', 'tokens.css'),
  path.join(srcRoot, 'styles', 'components.css'),
  path.join(srcRoot, 'styles', 'auth.css'),
  path.join(srcRoot, 'app', 'app-shell.css'),
  path.join(srcRoot, 'features', 'operator-workspace', 'OperatorWorkspaceCommon.tsx'),
  path.join(srcRoot, 'features', 'operator-workspace', 'operator-workspace.css'),
  path.join(srcRoot, 'features', 'operator-workspace', 'operator-workspace-refinements.css'),
  path.join(srcRoot, 'features', 'admin-routes', 'admin-routes.css'),
  path.join(srcRoot, 'features', 'knowledge', 'knowledge.css'),
  path.join(srcRoot, 'features', 'knowledge', 'KnowledgeReadOnlyPage.tsx'),
  path.join(srcRoot, 'features', 'runtime', 'runtime-evidence-audit.css'),
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

function relative(file) {
  return path.relative(repositoryRoot, file).split(path.sep).join('/')
}

function sourceFiles() {
  return walk(srcRoot)
    .filter((file) => SOURCE_EXTENSIONS.includes(path.extname(file)))
    .filter((file) => !file.endsWith('.d.ts'))
}

function resolveCandidate(candidate) {
  const candidates = [
    candidate,
    ...SOURCE_EXTENSIONS.map((extension) => `${candidate}${extension}`),
    ...SOURCE_EXTENSIONS.map((extension) => path.join(candidate, `index${extension}`)),
  ]
  return candidates.find((file) => fs.existsSync(file) && fs.statSync(file).isFile()) ?? null
}

function resolveImport(importer, specifier) {
  if (specifier.startsWith('@/')) return resolveCandidate(path.join(srcRoot, specifier.slice(2)))
  if (specifier.startsWith('.')) return resolveCandidate(path.resolve(path.dirname(importer), specifier))
  return null
}

function importsFor(file) {
  const content = fs.readFileSync(file, 'utf8')
  const imports = []
  for (const match of content.matchAll(IMPORT_RE)) {
    const resolved = resolveImport(file, match[1] ?? match[2])
    if (resolved) imports.push(resolved)
  }
  return imports
}

function reachableFiles() {
  const reachable = new Set()
  const queue = [entrypoint]
  while (queue.length) {
    const current = queue.pop()
    if (!current || reachable.has(current) || !fs.existsSync(current)) continue
    reachable.add(current)
    for (const imported of importsFor(current)) if (!reachable.has(imported)) queue.push(imported)
  }
  return reachable
}

function externalImports(files) {
  const imports = new Set()
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    for (const match of content.matchAll(IMPORT_RE)) {
      const specifier = match[1] ?? match[2]
      if (!specifier || specifier.startsWith('.') || specifier.startsWith('@/')) continue
      imports.add(specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0])
    }
  }
  return imports
}

function readJson(file, failures, label) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch (error) {
    failures.push(`${label} is missing or invalid: ${error instanceof Error ? error.message : String(error)}`)
    return null
  }
}

function assertSingleVisualAuthority(files, failures) {
  const creators = []
  const providers = []
  const baselines = []
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    if (/\bcreateTheme\s*\(/.test(content)) creators.push(relative(file))
    if (/<ThemeProvider\b/.test(content)) providers.push(relative(file))
    if (/<CssBaseline\b/.test(content)) baselines.push(relative(file))
  }
  if (creators.length !== 1 || creators[0] !== relative(themePath)) failures.push(`MUI theme authority must be exactly ${relative(themePath)}: ${creators.join(', ') || 'none'}`)
  if (providers.length !== 1 || providers[0] !== relative(themeProviderPath)) failures.push(`MUI ThemeProvider authority must be exactly ${relative(themeProviderPath)}: ${providers.join(', ') || 'none'}`)
  if (baselines.length !== 1 || baselines[0] !== relative(themeProviderPath)) failures.push(`MUI CssBaseline authority must be exactly ${relative(themeProviderPath)}: ${baselines.join(', ') || 'none'}`)
}

function assertPresentationConvergence(files, failures) {
  if (!fs.existsSync(presentationPath)) {
    failures.push(`operator presentation authority is missing: ${relative(presentationPath)}`)
    return
  }
  const presentation = fs.readFileSync(presentationPath, 'utf8')
  for (const name of [
    'OperatorPageBoundary', 'OperatorLoadingState', 'RouteLoadingState',
    'OperatorEmptyState', 'OperatorErrorNotice', 'OperatorFactGrid',
    'OperatorSectionHeading', 'OperatorStatusLine', 'OperatorTechnicalDisclosure',
    'normalizeOperatorTone', 'operatorToneColor', 'operatorTonePalettePath',
    'operatorAlertSeverity', 'operatorScrollBehavior', 'operatorErrorMessage',
  ]) {
    if (!new RegExp(`export\\s+(?:function|type)\\s+${name}\\b`).test(presentation)) {
      failures.push(`operator presentation authority is missing export ${name}`)
    }
  }

  const forbiddenDefinitions = /\b(?:function|const|type|interface)\s+(WorkspacePresentation|WorkspaceStatusLine|WorkspaceSectionHeading|WorkspaceLoading|FullPageBoundary|TechnicalDisclosure|StatusCount|safeTone|toneColor|providerLabel|channelLabel|safeRecord|safeRecordArray|safeWorkspaceRecord|workspaceText|workspaceNumber|textValue|numberValue)\b/g
  const directDisclosure = /\b(?:Accordion|AccordionSummary|AccordionDetails)\b/
  const violations = []
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    const fileName = relative(file)
    if (file !== presentationPath && directDisclosure.test(content)) violations.push(`${fileName}: direct Accordion disclosure`)
    for (const match of content.matchAll(forbiddenDefinitions)) violations.push(`${fileName}: ${match[1]}`)
    if (/className\s*:\s*['"]is-ai['"]/.test(content)) violations.push(`${fileName}: stale is-ai presentation class`)
  }
  if (violations.length) failures.push(`duplicate or retired presentation responsibility: ${violations.join(' | ')}`)

  const shell = fs.readFileSync(path.join(srcRoot, 'app', 'AppShell.tsx'), 'utf8')
  if (/component=["']main["']/.test(shell)) failures.push('AppShell must not own a main landmark; route pages own main')
  for (const file of [
    'features/operator-workspace/OperatorWorkspacePage.tsx',
    'features/knowledge/KnowledgePage.tsx',
    'features/channels/ChannelsPage.tsx',
    'features/runtime/RuntimePage.tsx',
    'features/control-tower/ControlTowerPage.tsx',
  ]) {
    const absolute = path.join(srcRoot, file)
    if (!fs.existsSync(absolute) || !/component=["']main["']/.test(fs.readFileSync(absolute, 'utf8'))) {
      failures.push(`canonical route page must own main landmark: webapp/src/${file}`)
    }
  }
}

function assertWorkspaceConvergence(failures) {
  if (!fs.existsSync(workspacePath)) {
    failures.push('canonical OperatorWorkspacePage is missing')
    return
  }
  const content = fs.readFileSync(workspacePath, 'utf8')
  const lineCount = content.split(/\r?\n/).length
  if (lineCount > 450) failures.push(`OperatorWorkspacePage exceeds the bounded orchestration limit: ${lineCount} lines`)
  for (const required of [
    './OperatorWorkspaceQueue',
    './OperatorWorkspaceCase',
    './OperatorWorkspaceActions',
    './operatorWorkspaceState',
  ]) if (!content.includes(required)) failures.push(`OperatorWorkspacePage must compose canonical workspace module: ${required}`)
  if (/function\s+(QueueRow|ConversationPanel|CaseSpine|EvidencePanel|ActionPanel|EmptyState|ErrorNotice|LoadingState)\b/.test(content)) {
    failures.push('OperatorWorkspacePage reabsorbed a view or presentation responsibility')
  }
  if (/thread-v2|thread-page|workspace-v2|new-workspace/.test(content)) failures.push('parallel Workspace implementation marker returned')
}

function assertKnowledgeConvergence(failures) {
  if (!fs.existsSync(knowledgePath) || !fs.existsSync(knowledgeRoutePath)) {
    failures.push('canonical Knowledge page or route is missing')
    return
  }
  const page = fs.readFileSync(knowledgePath, 'utf8')
  const route = fs.readFileSync(knowledgeRoutePath, 'utf8')
  if (!/KnowledgePage\(\{\s*canManage\s*\}/.test(page)) failures.push('KnowledgePage must own read and manage modes through canManage')
  if (!/<LazyKnowledgePage\s+canManage=\{canManage\}/.test(route)) failures.push('knowledge route must pass canManage to the one KnowledgePage')
  if (/KnowledgeReadOnlyPage/.test(route + page)) failures.push('duplicate KnowledgeReadOnlyPage reference returned')
}

function assertDependencies(files, authority, failures) {
  const manifest = readJson(packagePath, failures, 'package.json')
  const lock = readJson(lockPath, failures, 'package-lock.json')
  if (!manifest || !lock) return
  const allDependencies = { ...(manifest.dependencies ?? {}), ...(manifest.devDependencies ?? {}) }
  for (const dependency of Object.keys(allDependencies)) {
    if (FORBIDDEN_UI_PACKAGES.has(dependency) || dependency.startsWith('@tailwindcss/')) failures.push(`parallel UI framework dependency is forbidden: ${dependency}`)
    if (dependency.startsWith('@mui/') && !APPROVED_MUI_DIRECT_PACKAGES.has(dependency)) failures.push(`unapproved direct MUI package: ${dependency}`)
    if (dependency.startsWith('@emotion/') && !APPROVED_EMOTION_DIRECT_PACKAGES.has(dependency)) failures.push(`unapproved direct Emotion package: ${dependency}`)
  }
  for (const [dependency, version] of Object.entries(authority?.runtime_packages ?? {})) {
    if ((manifest.dependencies ?? {})[dependency] !== version) failures.push(`selected visual dependency must be pinned exactly: ${dependency}@${version}`)
    const locked = lock.packages?.[`node_modules/${dependency}`]?.version
    if (locked !== version) failures.push(`package-lock dependency is missing or stale: ${dependency}@${version}; found ${locked ?? 'none'}`)
  }
  if ((manifest.overrides ?? {})['react-is'] !== authority?.react_compatibility?.react_is_override) failures.push('React compatibility override for react-is is stale')
  const root = lock.packages?.[''] ?? {}
  for (const section of ['dependencies', 'devDependencies']) {
    const manifestSection = manifest[section] ?? {}
    const lockSection = root[section] ?? {}
    for (const [dependency, version] of Object.entries(manifestSection)) if (lockSection[dependency] !== version) failures.push(`package-lock root is stale: ${section}.${dependency}`)
    for (const dependency of Object.keys(lockSection)) if (!Object.hasOwn(manifestSection, dependency)) failures.push(`package-lock root retains removed dependency: ${section}.${dependency}`)
  }
  const consumed = externalImports(files)
  for (const dependency of REQUIRED_VISUAL_SUPPORT_PACKAGES) consumed.add(dependency)
  for (const file of walk(webappRoot).filter((candidate) => /(?:vite\.config|playwright\.config|eslint\.config|\.mjs$)/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    for (const match of content.matchAll(IMPORT_RE)) {
      const specifier = match[1] ?? match[2]
      if (!specifier || specifier.startsWith('.') || specifier.startsWith('@/') || specifier.startsWith('node:')) continue
      consumed.add(specifier.startsWith('@') ? specifier.split('/').slice(0, 2).join('/') : specifier.split('/')[0])
    }
  }
  for (const dependency of Object.keys(manifest.dependencies ?? {})) if (!consumed.has(dependency)) failures.push(`unused runtime dependency: ${dependency}`)
}

const failures = []
for (const forbidden of forbiddenPaths) if (fs.existsSync(forbidden)) failures.push(`retired path exists: ${relative(forbidden)}`)
const workflowDir = path.join(repositoryRoot, '.github', 'workflows')
if (fs.existsSync(workflowDir)) failures.push(`GitHub Actions are retired; .github/workflows must be absent: ${walk(workflowDir).map(relative).join(', ') || 'empty directory exists'}`)

const authority = readJson(muiAuthorityPath, failures, 'MUI visual authority')
if (authority?.schema !== 'nexus.mui-visual-authority.v1') failures.push(`unexpected MUI authority schema: ${authority?.schema}`)
if (authority?.decision?.selected_package !== '@mui/material' || authority?.decision?.selected_version !== '9.2.0') failures.push('MUI authority must select @mui/material@9.2.0')

const files = sourceFiles()
const reachable = reachableFiles()
const unreachable = files.filter((file) => !reachable.has(file)).map(relative)
if (unreachable.length) failures.push(`unreachable production files: ${unreachable.join(', ')}`)

const duplicateExports = new Map()
for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
  const content = fs.readFileSync(file, 'utf8')
  for (const match of content.matchAll(GENERIC_EXPORT_RE)) {
    const owners = duplicateExports.get(match[1]) ?? []
    owners.push(relative(file))
    duplicateExports.set(match[1], owners)
  }
  if (FORBIDDEN_PARALLEL_PATH_RE.test(relative(file))) failures.push(`parallel UI implementation path is forbidden: ${relative(file)}`)
  if (FORBIDDEN_ROUTE_RE.test(content)) failures.push(`parallel UI route is forbidden: ${relative(file)}`)
  if (/\bfetch\s*\(/.test(content) && relative(file) !== 'webapp/src/lib/apiClient.ts') failures.push(`generic HTTP transport outside apiClient.ts: ${relative(file)}`)
  if (file !== themePath) {
    const colors = [...content.matchAll(RAW_COLOR_RE)].map((match) => match[0])
    RAW_COLOR_RE.lastIndex = 0
    if (colors.length) failures.push(`raw color literal outside nexusTheme.ts: ${relative(file)}: ${[...new Set(colors)].join(', ')}`)
  }
}
for (const [name, owners] of duplicateExports) if (owners.length > 1) failures.push(`duplicate UI authorities: ${name}: ${owners.join(', ')}`)

const cssFiles = files.filter((file) => file.endsWith('.css')).map(relative).sort()
for (const file of cssFiles) if (!ALLOWED_SOURCE_CSS.has(file)) failures.push(`route or component CSS is forbidden under MUI authority: ${file}`)
for (const file of ALLOWED_SOURCE_CSS) if (!cssFiles.includes(file)) failures.push(`bounded global CSS is missing: ${file}`)
for (const file of files.filter((candidate) => candidate.endsWith('.css'))) {
  const content = fs.readFileSync(file, 'utf8')
  if (/--(?:bg|panel|panel-soft|line|line-strong|text|muted|brand|brand-2|success|warning|danger|shadow|radius)\s*:/.test(content)) failures.push(`retired CSS variable authority returned: ${relative(file)}`)
  if (/transition\s*:\s*all\b/i.test(content)) failures.push(`transition: all is forbidden: ${relative(file)}`)
  if (/\.Mui[A-Za-z0-9_-]+/.test(content)) failures.push(`MUI component overrides must live in nexusTheme.ts: ${relative(file)}`)
}

assertSingleVisualAuthority(files, failures)
assertPresentationConvergence(files, failures)
assertWorkspaceConvergence(failures)
assertKnowledgeConvergence(failures)
assertDependencies(files, authority, failures)

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
  ui_authority: '@mui/material@9.2.0',
  theme_authority: relative(themePath),
  operator_presentation_authority: relative(presentationPath),
  source_css: [...ALLOWED_SOURCE_CSS].sort(),
  knowledge_implementation: relative(knowledgePath),
  workspace_orchestrator: relative(workspacePath),
  duplicate_residue: 'absent',
}, null, 2))
