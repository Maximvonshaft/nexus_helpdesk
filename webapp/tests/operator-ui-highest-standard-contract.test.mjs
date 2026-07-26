import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const shell = read('src/app/AppShell.tsx')
const layoutMode = read('src/app/useOperatorLayoutMode.tsx')
const layoutProvider = read('src/app/OperatorLayoutProvider.tsx')
const workspace = read('src/features/operator-workspace/OperatorWorkspacePage.tsx')
const queue = read('src/features/operator-workspace/OperatorWorkspaceQueue.tsx')
const casePane = read('src/features/operator-workspace/OperatorWorkspaceCase.tsx')
const theme = read('src/theme/nexusTheme.ts')
const sizeReport = read('scripts/size-report.mjs')
const routeSplitting = read('scripts/assert-route-splitting.mjs')
const webcallRoute = read('src/routes/webcall.tsx')
const webcallLazy = read('src/features/webcall/lazy.tsx')
const presentation = read('src/lib/operatorWorkspacePresentation.ts')
const closure = read('src/features/operator-workspace/OperatorWorkspaceClosure.tsx')


test('text enlargement and viewport width resolve through one responsive-layout authority', () => {
  assert.match(layoutMode, /OperatorLayoutContext/)
  assert.match(layoutMode, /useOperatorLayoutMode/)
  assert.match(layoutProvider, /ResizeObserver/)
  assert.match(layoutProvider, /width:\s*'1rem'/)
  assert.match(layoutProvider, /textScale/)
  assert.match(layoutProvider, /desktopLayout/)
  assert.match(layoutProvider, /OperatorLayoutProvider/)
  assert.match(shell, /<OperatorLayoutProvider>/)
  assert.match(shell, /useOperatorLayoutMode\(\)/)
  assert.match(workspace, /useOperatorLayoutMode\(\)/)
  assert.match(queue, /desktopLayout/)
  assert.match(casePane, /desktopLayout/)
  assert.doesNotMatch(shell, /const desktopShell = useMediaQuery/)
})


test('form and compact status controls grow with enlarged text instead of clipping it', () => {
  assert.match(theme, /MuiSelect:[\s\S]*minHeight:\s*'44px !important'/)
  assert.match(theme, /MuiSelect:[\s\S]*paddingTop:\s*'1em !important'/)
  assert.match(theme, /MuiSelect:[\s\S]*paddingBottom:\s*'0\.65em !important'/)
  assert.match(theme, /MuiSwitch:[\s\S]*switchBase:[\s\S]*minHeight:\s*44/)
  assert.match(theme, /MuiChip:[\s\S]*height:\s*'auto'/)
  assert.match(theme, /MuiChip:[\s\S]*whiteSpace:\s*'normal'/)
})


test('first-screen budget follows the Vite manifest static closure without filename guesses', () => {
  assert.match(sizeReport, /\.vite.*manifest\.json/)
  assert.match(sizeReport, /record\?\.imports/)
  assert.match(sizeReport, /initialStaticFiles/)
  assert.doesNotMatch(sizeReport, /lazy\|route\|vendor/)
})


test('the LiveKit call surface is reachable only through the existing dynamic route graph', () => {
  assert.match(routeSplitting, /src\/features\/webcall\/lazy\.tsx/)
  assert.match(routeSplitting, /livekit/i)
  assert.match(webcallRoute, /lazy\(\(\) => import\('@\/features\/webcall\/lazy'\)\)/)
  assert.doesNotMatch(webcallRoute, /WebCallPage/)
  assert.match(webcallLazy, /WebCallOperatorContext/)
  assert.match(webcallLazy, /WebCallPage/)
})


test('primary workspace facts consume business presentation instead of raw backend enums', () => {
  assert.match(presentation, /closureRequirementPresentation/)
  assert.match(presentation, /scenarioPresentation/)
  assert.match(closure, /closureRequirementPresentation/)
  assert.match(closure, /scenarioPresentation/)
  assert.match(casePane, /sourceStatusPresentation/)
  assert.match(casePane, /priorityPresentation/)
})
