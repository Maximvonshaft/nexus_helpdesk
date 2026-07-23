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
const themePath = path.join(srcRoot, 'theme', 'nexusTheme.ts')
const themeProviderPath = path.join(srcRoot, 'theme', 'NexusThemeProvider.tsx')
const operatorPresentationPath = path.join(srcRoot, 'app', 'OperatorPresentation.tsx')
const knowledgePath = path.join(srcRoot, 'features', 'knowledge', 'KnowledgePage.tsx')
const knowledgeRoutePath = path.join(srcRoot, 'routes', 'knowledge.tsx')
const workspacePath = path.join(srcRoot, 'features', 'operator-workspace', 'OperatorWorkspacePage.tsx')
const workspaceCommonPath = path.join(srcRoot, 'features', 'operator-workspace', 'OperatorWorkspaceCommon.tsx')
const canonicalWorkflowPath = path.join(repositoryRoot, '.github', 'workflows', 'canonical-acceptance.yml')
const controlledCandidateWorkflowPath = path.join(repositoryRoot, '.github', 'workflows', 'controlled-candidate-convergence.yml')

const SOURCE_EXTENSIONS = new Set(['.ts', '.tsx', '.css'])
const SCRIPT_EXTENSIONS = new Set(['.js', '.mjs', '.cjs', '.ts'])
const IGNORED_DIRECTORIES = new Set(['node_modules', 'dist', 'coverage', 'playwright-report', 'test-results', '.git'])
const IMPORT_RE = /(?:import|export)\s+(?:[^'"()]*?\s+from\s+)?["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)/g
const EXPORTED_PRIMITIVE_RE = /export\s+(?:const|function|class)\s+(AppShell|AppNavigation|Button|ButtonLink|Badge|Card|Field|Input|Select|Textarea|ConfirmDialog|EmptyState|ErrorSummary|TechnicalDetails|PageHeader|StatusIndicator|Count)\b/g
const RETIRED_LOCAL_HELPER_RE = /\bfunction\s+(EmptyState|ErrorNotice|ErrorSummary|LoadingState|FactGrid|statusColor|muiStatusColor|errorCopy|scrollBehavior)\b/g
const CANONICAL_OPERATOR_HELPER_RE = /export\s+function\s+(OperatorEmptyState|OperatorErrorNotice|OperatorLoadingState|RouteLoadingState|OperatorFactGrid|operatorToneColor|operatorTonePalettePath|operatorScrollBehavior|operatorErrorMessage)\b/g
const LEGACY_PALETTE_RE = /--(?:bg|panel|panel-soft|line|line-strong|text|muted|brand|brand-2|success|warning|danger|shadow|radius)\s*:/g
const LEGACY_SELECTOR_RE = /(^|[,\s{])\.(?:button|badge|card)(?=[\s,{.:#\[])/gm
const RETIRED_SOURCE_LITERAL_RE = /\b(?:nd-app-boundary-state|empty-state|nd-button|nd-field|nd-badge)\b/
const RAW_COLOR_RE = /#[0-9a-f]{3,8}\b|rgba?\(\s*\d|hsla?\(\s*\d/gi
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
const APPROVED_MUI_DIRECT_PACKAGES = new Set(['@mui/material', '@mui/icons-material'])
const APPROVED_EMOTION_DIRECT_PACKAGES = new Set(['@emotion/react', '@emotion/styled'])
const REQUIRED_VISUAL_SUPPORT_PACKAGES = new Set(['@emotion/react', '@emotion/styled', 'react-is'])
const ALLOWED_SOURCE_CSS = new Set(['webapp/src/styles.css', 'webapp/src/a11y.css'])
const FORBIDDEN_PATHS = [
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
  path.join(srcRoot, 'features', 'operator-workspace', 'operator-workspace.css'),
  path.join(srcRoot, 'features', 'operator-workspace', 'operator-workspace-refinements.css'),
  path.join(srcRoot, 'features', 'admin-routes', 'admin-routes.css'),
  path.join(srcRoot, 'features', 'knowledge', 'knowledge.css'),
  path.join(srcRoot, 'features', 'knowledge', 'KnowledgeReadOnlyPage.tsx'),
  path.join(srcRoot, 'features', 'runtime', 'runtime-evidence-audit.css'),
  workspaceCommonPath,
]

function walk(directory) {
  if (!fs.existsSync(directory)) return []
  const files = []
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && IGNORED_DIRECTORIES.has(entry.name)) continue
    const absolute = path.join(directory, entry.name)
    if (entry.isDirectory()) files.push(...walk(absolute))
    else files.push(absolute)
  }
  return files
}

function normalize(value) {
  return path.normalize(value)
}

function relative(file) {
  return path.relative(repositoryRoot, file).split(path.sep).join('/')
}

function sourceFiles() {
  return walk(srcRoot)
    .filter((file) => SOURCE_EXTENSIONS.has(path.extname(file)))
    .filter((file) => !file.endsWith('.d.ts'))
    .map(normalize)
}

function resolveCandidate(candidate) {
  const candidates = [
    candidate,
    ...[...SOURCE_EXTENSIONS].map((extension) => `${candidate}${extension}`),
    ...[...SOURCE_EXTENSIONS].map((extension) => path.join(candidate, `index${extension}`)),
  ]
  return candidates.find((file) => fs.existsSync(file) && fs.statSync(file).isFile()) ?? null
}

function resolveImport(importer, specifier) {
  if (specifier.startsWith('@/')) return resolveCandidate(path.join(srcRoot, specifier.slice(2)))
  if (specifier.startsWith('.')) return resolveCandidate(path.resolve(path.dirname(importer), specifier))
  return null
}

function importsFor(file) {
  const imports = []
  const content = fs.readFileSync(file, 'utf8')
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

function readJson(file, label, failures) {
  if (!fs.existsSync(file)) {
    failures.push(`${label} is missing: ${relative(file)}`)
    return null
  }
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch (error) {
    failures.push(`${label} is invalid JSON: ${error instanceof Error ? error.message : String(error)}`)
    return null
  }
}

function assertCanonicalActions(failures) {
  const workflowDir = path.dirname(canonicalWorkflowPath)
  const workflowFiles = fs.existsSync(workflowDir)
    ? walk(workflowDir).filter((file) => fs.statSync(file).isFile()).map(relative).sort()
    : []
  const expected = [relative(canonicalWorkflowPath), relative(controlledCandidateWorkflowPath)].sort()
  if (JSON.stringify(workflowFiles) !== JSON.stringify(expected)) {
    failures.push(`GitHub Actions authority set is invalid: expected=${expected.join(', ')} actual=${workflowFiles.join(', ') || 'none'}`)
    return
  }

  const canonical = fs.readFileSync(canonicalWorkflowPath, 'utf8')
  for (const marker of ['name: Canonical Acceptance', 'pull_request:', 'push:', 'workflow_dispatch:', 'required-gate:']) {
    if (!canonical.includes(marker)) failures.push(`canonical acceptance workflow marker is missing: ${marker}`)
  }

  const candidate = fs.readFileSync(controlledCandidateWorkflowPath, 'utf8')
  for (const marker of [
    'name: controlled-candidate-convergence',
    'workflow_run:',
    '- Canonical Acceptance',
    "github.event.workflow_run.conclusion == 'success'",
    "github.event.workflow_run.event == 'push'",
    "github.event.workflow_run.head_branch == 'main'",
    'scripts/release/run_controlled_rc_gate.sh',
    'scripts/release/run_controlled_recovery_gate.sh',
    'controlled-candidate.env',
  ]) {
    if (!candidate.includes(marker)) failures.push(`controlled candidate workflow marker is missing: ${marker}`)
  }
  for (const forbidden of ['pull_request:', 'workflow_dispatch:', 'issue_comment:', 'repository_dispatch:']) {
    if (candidate.includes(forbidden)) failures.push(`controlled candidate workflow contains forbidden trigger: ${forbidden}`)
  }
}

function assertNoParallelImplementation(files, failures) {
  const forbiddenNames = files.map(relative).filter((file) => FORBIDDEN_PARALLEL_PATH_RE.test(file))
  if (forbiddenNames.length) failures.push(`parallel UI implementation path is forbidden: ${forbiddenNames.join(', ')}`)
  const forbiddenRoutes = files
    .filter((file) => /\.(?:ts|tsx)$/.test(file))
    .filter((file) => FORBIDDEN_ROUTE_RE.test(fs.readFileSync(file, 'utf8')))
    .map(relative)
  if (forbiddenRoutes.length) failures.push(`parallel UI route is forbidden: ${forbiddenRoutes.join(', ')}`)
}

function assertDuplicatePrimitiveAuthorities(files, failures) {
  const owners = new Map()
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    for (const match of content.matchAll(EXPORTED_PRIMITIVE_RE)) {
      const entries = owners.get(match[1]) ?? []
      entries.push(relative(file))
      owners.set(match[1], entries)
    }
  }
  const duplicates = [...owners.entries()].filter(([, entries]) => entries.length > 1)
  if (duplicates.length) {
    failures.push(`duplicate UI authorities: ${duplicates.map(([name, entries]) => `${name}: ${entries.join(', ')}`).join(' | ')}`)
  }
}

function assertCanonicalNavigation(files, failures) {
  const owners = files
    .filter((file) => /\.(?:ts|tsx)$/.test(file))
    .filter((file) => fs.readFileSync(file, 'utf8').includes('APP_NAVIGATION'))
    .map(relative)
  const allowed = new Set(['webapp/src/app/navigation.ts', 'webapp/src/app/AppNavigation.tsx'])
  const unexpected = owners.filter((file) => !allowed.has(file))
  if (unexpected.length) failures.push(`unexpected navigation authority: ${unexpected.join(', ')}`)

  if (!fs.existsSync(workspacePath)) return
  const workspace = fs.readFileSync(workspacePath, 'utf8')
  if (/function\s+AppNavigation\b/.test(workspace) || /className=["']operator-app-header["']/.test(workspace)) {
    failures.push('OperatorWorkspacePage still owns a second application shell or navigation')
  }
  if (workspace.includes('/webchat?tab=')) failures.push('OperatorWorkspacePage still links through compatibility tabs')
}

function assertCssAuthority(files, failures) {
  const cssFiles = files.filter((file) => file.endsWith('.css')).map(relative).sort()
  const unexpected = cssFiles.filter((file) => !ALLOWED_SOURCE_CSS.has(file))
  if (unexpected.length) failures.push(`route or component CSS is forbidden under MUI authority: ${unexpected.join(', ')}`)
  const missing = [...ALLOWED_SOURCE_CSS].filter((file) => !cssFiles.includes(file))
  if (missing.length) failures.push(`bounded global CSS is missing: ${missing.join(', ')}`)

  for (const file of files.filter((candidate) => candidate.endsWith('.css'))) {
    const content = fs.readFileSync(file, 'utf8')
    const fileName = relative(file)
    if (LEGACY_PALETTE_RE.test(content)) failures.push(`retired CSS variable authority returned: ${fileName}`)
    LEGACY_PALETTE_RE.lastIndex = 0
    if (LEGACY_SELECTOR_RE.test(content)) failures.push(`legacy primitive selector returned outside MUI: ${fileName}`)
    LEGACY_SELECTOR_RE.lastIndex = 0
    if (/transition\s*:\s*all\b/i.test(content)) failures.push(`transition: all is forbidden: ${fileName}`)
    if (/\.Mui[A-Za-z0-9_-]+/.test(content)) failures.push(`MUI component overrides must live in nexusTheme.ts, not CSS: ${fileName}`)
  }
}

function assertSourceVisualResidue(files, failures) {
  const retiredLiterals = []
  const rawColors = []
  const retiredHelpers = []
  const helperOwners = new Map()

  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    const fileName = relative(file)
    if (RETIRED_SOURCE_LITERAL_RE.test(content)) retiredLiterals.push(fileName)

    if (normalize(file) !== normalize(themePath)) {
      const colors = [...content.matchAll(RAW_COLOR_RE)].map((match) => match[0])
      if (colors.length) rawColors.push(`${fileName}: ${[...new Set(colors)].join(', ')}`)
    }
    RAW_COLOR_RE.lastIndex = 0

    for (const match of content.matchAll(RETIRED_LOCAL_HELPER_RE)) retiredHelpers.push(`${match[1]}: ${fileName}`)
    for (const match of content.matchAll(CANONICAL_OPERATOR_HELPER_RE)) {
      const entries = helperOwners.get(match[1]) ?? []
      entries.push(fileName)
      helperOwners.set(match[1], entries)
    }
  }

  if (retiredLiterals.length) failures.push(`retired visual class literal returned in source: ${retiredLiterals.join(', ')}`)
  if (rawColors.length) failures.push(`raw color literal outside nexusTheme.ts: ${rawColors.join(' | ')}`)
  if (retiredHelpers.length) failures.push(`route-private generic presentation helper is forbidden: ${retiredHelpers.join(' | ')}`)

  const expectedOwner = 'webapp/src/app/OperatorPresentation.tsx'
  for (const name of [
    'OperatorEmptyState',
    'OperatorErrorNotice',
    'OperatorLoadingState',
    'RouteLoadingState',
    'OperatorFactGrid',
    'operatorToneColor',
    'operatorTonePalettePath',
    'operatorScrollBehavior',
    'operatorErrorMessage',
  ]) {
    const owners = helperOwners.get(name) ?? []
    if (owners.length !== 1 || owners[0] !== expectedOwner) {
      failures.push(`operator presentation helper must be owned exactly once by ${expectedOwner}: ${name} -> ${owners.join(', ') || 'none'}`)
    }
  }
}

function assertKnowledgeConvergence(failures) {
  if (!fs.existsSync(knowledgePath)) failures.push('canonical KnowledgePage is missing')
  if (!fs.existsSync(knowledgeRoutePath)) failures.push('canonical knowledge route is missing')
  if (!fs.existsSync(knowledgePath) || !fs.existsSync(knowledgeRoutePath)) return

  const page = fs.readFileSync(knowledgePath, 'utf8')
  const route = fs.readFileSync(knowledgeRoutePath, 'utf8')
  if (!/KnowledgePage\(\{\s*canManage\s*\}/.test(page)) failures.push('KnowledgePage must own read and manage modes through canManage')
  if (!/<LazyKnowledgePage\s+canManage=\{canManage\}/.test(route)) failures.push('knowledge route must pass canManage to the one KnowledgePage')
  if (/KnowledgeReadOnlyPage/.test(route + page)) failures.push('duplicate KnowledgeReadOnlyPage reference returned')
}

function assertWorkspaceConvergence(failures) {
  if (!fs.existsSync(workspacePath)) {
    failures.push('canonical OperatorWorkspacePage is missing')
    return
  }
  if (fs.existsSync(workspaceCommonPath)) failures.push('retired OperatorWorkspaceCommon.tsx returned')

  const content = fs.readFileSync(workspacePath, 'utf8')
  const lineCount = content.split(/\r?\n/).length
  if (lineCount > 450) failures.push(`OperatorWorkspacePage exceeds bounded orchestration limit: ${lineCount} lines`)
  for (const required of [
    './OperatorWorkspaceQueue',
    './OperatorWorkspaceCase',
    './OperatorWorkspaceActions',
    './operatorWorkspaceState',
  ]) {
    if (!content.includes(required)) failures.push(`OperatorWorkspacePage must compose canonical workspace module: ${required}`)
  }
  if (content.includes('./OperatorWorkspaceCommon')) failures.push('OperatorWorkspacePage imports retired OperatorWorkspaceCommon')
  if (/function\s+(QueueRow|ConversationPanel|CaseSpine|EvidencePanel|ActionPanel|EmptyState|ErrorNotice|LoadingState)\b/.test(content)) {
    failures.push('OperatorWorkspacePage reabsorbed a presentation responsibility')
  }
  if (/thread-v2|thread-page|workspace-v2|new-workspace/.test(content)) failures.push('parallel Workspace implementation marker returned')
  if (/Accordion|TextField|useMutation/.test(content)) failures.push('OperatorWorkspacePage reabsorbed action/form UI responsibilities')
}

function assertSingleThemeAuthority(files, failures) {
  for (const [file, message] of [
    [themePath, 'single MUI theme is missing'],
    [themeProviderPath, 'single MUI provider is missing'],
    [operatorPresentationPath, 'operator presentation authority is missing'],
  ]) {
    if (!fs.existsSync(file)) failures.push(`${message}: ${relative(file)}`)
  }

  const themeCreators = []
  const themeProviders = []
  const cssBaselines = []
  for (const file of files.filter((candidate) => /\.(?:ts|tsx)$/.test(candidate))) {
    const content = fs.readFileSync(file, 'utf8')
    if (/\bcreateTheme\s*\(/.test(content)) themeCreators.push(relative(file))
    if (/<ThemeProvider\b/.test(content)) themeProviders.push(relative(file))
    if (/<CssBaseline\b/.test(content)) cssBaselines.push(relative(file))
  }
  if (themeCreators.length !== 1 || themeCreators[0] !== 'webapp/src/theme/nexusTheme.ts') {
    failures.push(`MUI theme authority must be exactly webapp/src/theme/nexusTheme.ts: ${themeCreators.join(', ') || 'none'}`)
  }
  if (themeProviders.length !== 1 || themeProviders[0] !== 'webapp/src/theme/NexusThemeProvider.tsx') {
    failures.push(`MUI ThemeProvider authority must be exactly webapp/src/theme/NexusThemeProvider.tsx: ${themeProviders.join(', ') || 'none'}`)
  }
  if (cssBaselines.length !== 1 || cssBaselines[0] !== 'webapp/src/theme/NexusThemeProvider.tsx') {
    failures.push(`MUI CssBaseline authority must be exactly webapp/src/theme/NexusThemeProvider.tsx: ${cssBaselines.join(', ') || 'none'}`)
  }
}

function assertRuntimeDependencies(files, authority, failures) {
  const manifest = readJson(packagePath, 'package.json', failures)
  const lock = readJson(lockPath, 'package-lock.json', failures)
  if (!manifest || !lock) return

  const dependencies = { ...(manifest.dependencies ?? {}), ...(manifest.devDependencies ?? {}) }
  for (const dependency of Object.keys(dependencies)) {
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

  const expectedPackages = authority?.runtime_packages ?? {}
  for (const [dependency, version] of Object.entries(expectedPackages)) {
    if ((manifest.dependencies ?? {})[dependency] !== version) {
      failures.push(`selected visual dependency must be pinned exactly: ${dependency}@${version}`)
    }
    const locked = lock.packages?.[`node_modules/${dependency}`]?.version
    if (locked !== version) failures.push(`package-lock dependency is missing or stale: ${dependency}@${version}; found ${locked ?? 'none'}`)
  }
  if ((manifest.overrides ?? {})['react-is'] !== authority?.react_compatibility?.react_is_override) {
    failures.push(`React 18 requires package.json overrides.react-is=${authority?.react_compatibility?.react_is_override}`)
  }

  const rootLock = lock.packages?.[''] ?? {}
  for (const section of ['dependencies', 'devDependencies']) {
    const manifestSection = manifest[section] ?? {}
    const lockSection = rootLock[section] ?? {}
    for (const [dependency, version] of Object.entries(manifestSection)) {
      if (lockSection[dependency] !== version) failures.push(`package-lock root is stale: ${section}.${dependency} must be ${version}`)
    }
    for (const dependency of Object.keys(lockSection)) {
      if (!Object.hasOwn(manifestSection, dependency)) failures.push(`package-lock root retains removed dependency: ${section}.${dependency}`)
    }
  }

  const consumed = externalImports(files)
  for (const dependency of REQUIRED_VISUAL_SUPPORT_PACKAGES) consumed.add(dependency)
  for (const file of walk(webappRoot).filter((candidate) => SCRIPT_EXTENSIONS.has(path.extname(candidate)))) {
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
}

const failures = []
for (const forbidden of FORBIDDEN_PATHS) {
  if (fs.existsSync(forbidden)) failures.push(`retired path exists: ${relative(forbidden)}`)
}
assertCanonicalActions(failures)

const muiAuthority = readJson(muiAuthorityPath, 'MUI visual authority contract', failures)
if (muiAuthority) {
  if (muiAuthority.schema !== 'nexus.mui-visual-authority.v1') failures.push(`unexpected MUI authority schema: ${muiAuthority.schema}`)
  if (muiAuthority.decision?.selected_package !== '@mui/material') failures.push('MUI authority must select @mui/material')
  if (muiAuthority.decision?.selected_version !== '9.2.0') failures.push(`MUI version must be exactly 9.2.0: ${muiAuthority.decision?.selected_version}`)
}

const files = sourceFiles()
const reachable = reachableFiles()
const unreachable = files.filter((file) => !reachable.has(file)).map(relative)
if (unreachable.length) failures.push(`unreachable production files: ${unreachable.join(', ')}`)

assertNoParallelImplementation(files, failures)
assertDuplicatePrimitiveAuthorities(files, failures)
assertCanonicalNavigation(files, failures)
assertCssAuthority(files, failures)
assertSourceVisualResidue(files, failures)
assertKnowledgeConvergence(failures)
assertWorkspaceConvergence(failures)
assertSingleThemeAuthority(files, failures)
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
  github_actions: [relative(canonicalWorkflowPath), relative(controlledCandidateWorkflowPath)],
  http_transport_authority: 'webapp/src/lib/apiClient.ts',
  ui_authority: '@mui/material@9.2.0',
  theme_authority: 'webapp/src/theme/nexusTheme.ts',
  operator_presentation_authority: 'webapp/src/app/OperatorPresentation.tsx',
  source_css: [...ALLOWED_SOURCE_CSS].sort(),
  knowledge_implementation: 'webapp/src/features/knowledge/KnowledgePage.tsx',
  workspace_orchestrator: 'webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx',
  migration_status: muiAuthority?.decision?.status,
}, null, 2))
