import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const src = path.join(root, 'src')
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8')

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name)
    return entry.isDirectory() ? walk(absolute) : [absolute]
  })
}

const production = walk(src).filter((file) => /\.(?:ts|tsx)$/.test(file))
const presentationPath = path.join(src, 'app', 'OperatorPresentation.tsx')
const casePath = path.join(src, 'features', 'operator-workspace', 'OperatorWorkspaceCase.tsx')
const allowedFullPageOwners = new Set([
  presentationPath,
  path.join(src, 'app', 'AppShell.tsx'),
  path.join(src, 'routes', 'login.tsx'),
  path.join(src, 'features', 'webcall', 'WebCallPage.tsx'),
  path.join(src, 'theme', 'nexusTheme.ts'),
])
const retiredLocalHelper = /\bfunction\s+(EmptyState|ErrorNotice|ErrorSummary|LoadingState|FactGrid|statusColor|muiStatusColor|errorCopy|scrollBehavior)\b/g
const retiredClassLiteral = /\b(?:nd-app-boundary-state|empty-state|nd-button|nd-field|nd-badge)\b/
const forbiddenDefinitions = /\b(?:function|const|type|interface)\s+(WorkspacePresentation|WorkspaceStatusLine|WorkspaceSectionHeading|WorkspaceLoading|FullPageBoundary|TechnicalDisclosure|StatusCount|safeTone|toneColor|providerLabel|channelLabel|safeRecord|safeRecordArray|safeWorkspaceRecord|workspaceText|workspaceNumber|textValue|numberValue)\b/g

test('renaming cannot recreate retired generic presentation responsibilities', () => {
  const violations = []
  for (const file of production) {
    const source = fs.readFileSync(file, 'utf8')
    const relative = path.relative(root, file).split(path.sep).join('/')
    if (file !== presentationPath && /\b(?:Accordion|AccordionSummary|AccordionDetails)\b/.test(source)) {
      violations.push(`${relative}: direct Accordion disclosure`)
    }
    if (retiredClassLiteral.test(source)) violations.push(`${relative}: retired visual class literal`)
    for (const match of source.matchAll(retiredLocalHelper)) violations.push(`${relative}: ${match[1]}`)
    retiredLocalHelper.lastIndex = 0
    if (file !== presentationPath && /component=["']dl["']/.test(source)) violations.push(`${relative}: direct fact grid`)
    if (file !== presentationPath && file !== casePath && /borderRadius\s*:\s*["']50%["']/.test(source)) violations.push(`${relative}: generic status marker`)
    if (!allowedFullPageOwners.has(file) && /minHeight\s*:\s*["']100dvh["']/.test(source)) violations.push(`${relative}: route-private full-page layout`)
    for (const match of source.matchAll(forbiddenDefinitions)) violations.push(`${relative}: ${match[1]}`)
    if (/className\s*:\s*['"]is-ai['"]/.test(source)) violations.push(`${relative}: stale is-ai class`)
  }
  assert.deepEqual(violations, [])
})

test('navigation ownership remains canonical', () => {
  const owners = production
    .filter((file) => fs.readFileSync(file, 'utf8').includes('APP_NAVIGATION'))
    .map((file) => path.relative(root, file).split(path.sep).join('/'))
    .sort()
  assert.deepEqual(owners, ['src/app/AppNavigation.tsx', 'src/app/navigation.ts'])
})

test('operator presentation is the sole generic owner consumed by routes', () => {
  const presentation = read('src/app/OperatorPresentation.tsx')
  for (const name of [
    'OperatorPageBoundary',
    'OperatorLoadingState',
    'RouteLoadingState',
    'OperatorEmptyState',
    'OperatorErrorNotice',
    'OperatorFactGrid',
    'OperatorSectionHeading',
    'OperatorStatusLine',
    'OperatorTechnicalDisclosure',
  ]) assert.match(presentation, new RegExp(`export function ${name}`))

  assert.match(read('src/features/runtime/RuntimePage.tsx'), /OperatorTechnicalDisclosure/)
  assert.match(read('src/features/channels/ChannelsPage.tsx'), /OperatorTechnicalDisclosure/)
  assert.match(read('src/features/operator-workspace/OperatorWorkspaceCase.tsx'), /OperatorTechnicalDisclosure/)
  assert.match(read('src/features/operator-workspace/OperatorWorkspaceActions.tsx'), /OperatorTechnicalDisclosure/)
  assert.match(read('src/features/control-tower/ControlTowerPage.tsx'), /OperatorStatusLine/)
})

test('safe value and channel semantics have one owner', () => {
  const format = read('src/lib/format.ts')
  const supportStatus = read('src/lib/supportStatus.ts')
  const runtimeAudit = read('src/features/runtime/RuntimeEvidenceAudit.tsx')
  const actions = read('src/features/operator-workspace/OperatorWorkspaceActions.tsx')
  const channels = read('src/features/channels/ChannelsPage.tsx')
  const shell = read('src/app/AppShell.tsx')

  for (const name of ['recordValue', 'recordArrayValue', 'stringValue', 'finiteNumber']) {
    assert.match(format, new RegExp(`export function ${name}`))
  }
  assert.match(supportStatus, /export function channelPresentation/)
  assert.match(runtimeAudit, /recordArrayValue/)
  assert.match(actions, /recordValue/)
  assert.match(channels, /channelPresentation/)
  assert.match(shell, /channelPresentation/)
})

test('workspace compatibility layer is deleted and orchestration remains bounded', () => {
  assert.equal(fs.existsSync(path.join(src, 'features/operator-workspace/OperatorWorkspaceCommon.tsx')), false)
  const page = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
  assert.ok(page.split(/\r?\n/).length <= 450)
  assert.match(page, /OperatorWorkspaceActions/)
  assert.doesNotMatch(page, /Accordion|TextField|useMutation|function\s+ActionPanel/)
})
