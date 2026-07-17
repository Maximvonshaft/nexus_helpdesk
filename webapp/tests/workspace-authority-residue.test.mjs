import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const webappRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const srcRoot = path.join(webappRoot, 'src')
const workspaceRoot = path.join(srcRoot, 'features', 'operator-workspace')

function read(relative) {
  return fs.readFileSync(path.join(srcRoot, relative), 'utf8')
}

function sourceFiles(directory) {
  if (!fs.existsSync(directory)) return []
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name)
    return entry.isDirectory() ? sourceFiles(absolute) : [absolute]
  }).filter((file) => /\.(?:ts|tsx)$/.test(file))
}

test('workspace has one presentation, formatting and action authority', () => {
  assert.equal(
    fs.existsSync(path.join(workspaceRoot, 'OperatorWorkspaceCommon.tsx')),
    false,
    'retired OperatorWorkspaceCommon.tsx must remain absent',
  )

  const page = read('features/operator-workspace/OperatorWorkspacePage.tsx')
  assert.match(page, /from ['"]\.\/OperatorWorkspaceActions['"]/)
  assert.doesNotMatch(page, /function\s+(?:ActionPanel|actionDisabledReason)\b/)
  assert.doesNotMatch(page, /type\s+(?:SpeedafActionKind|ActionResultEnvelope|CancelPreviewBinding)\b/)

  const presentation = read('app/OperatorPresentation.tsx')
  for (const name of [
    'OperatorSectionHeading',
    'OperatorStatusLine',
    'OperatorTechnicalDisclosure',
    'OperatorFactGrid',
    'operatorAlertSeverity',
  ]) {
    assert.match(presentation, new RegExp(`export\\s+function\\s+${name}\\b`))
  }

  const format = read('lib/format.ts')
  for (const name of ['recordValue', 'recordArrayValue', 'stringValue', 'finiteNumber']) {
    assert.match(format, new RegExp(`export\\s+function\\s+${name}\\b`))
  }

  const state = read('features/operator-workspace/operatorWorkspaceState.ts')
  assert.match(state, /export\s+function\s+hasWorkspaceCapability\b/)

  const forbiddenDefinitions = /\b(?:function|const|type|interface)\s+(?:WorkspacePresentation|WorkspaceStatusLine|WorkspaceSectionHeading|safeWorkspaceRecord|workspaceText|workspaceNumber)\b/g
  const violations = []
  for (const file of sourceFiles(srcRoot)) {
    const content = fs.readFileSync(file, 'utf8')
    if (forbiddenDefinitions.test(content)) {
      violations.push(path.relative(webappRoot, file).split(path.sep).join('/'))
    }
    forbiddenDefinitions.lastIndex = 0
    if (/from\s+['"].*OperatorWorkspaceCommon['"]/.test(content)) {
      violations.push(`${path.relative(webappRoot, file).split(path.sep).join('/')}: retired import`)
    }
  }
  assert.deepEqual(violations, [])
})
