import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const main = read('src/main.tsx')
const theme = read('src/theme/nexusTheme.ts')
const provider = read('src/theme/NexusThemeProvider.tsx')
const a11yCss = read('src/a11y.css')

test('MUI buttons and icon buttons meet the shared target and focus contract', () => {
  assert.match(theme, /MuiButton:/)
  assert.match(theme, /MuiIconButton:/)
  assert.match(theme, /minHeight:\s*44/)
  assert.match(theme, /minWidth:\s*44/)
  assert.match(theme, /&:focus-visible/)
  assert.match(theme, /outlineOffset:\s*2/)
})

test('global accessibility foundation loads without runtime patchers', () => {
  assert.match(main, /import '@\/a11y\.css'/)
  assert.match(main, /NexusThemeProvider/)
  assert.match(provider, /<CssBaseline \/>/)
  assert.doesNotMatch(main, /a11yRuntime|initA11yRuntimeRepair/)
})

test('reduced motion is owned once by the MUI theme and CSS remains bounded', () => {
  assert.match(theme, /prefers-reduced-motion/)
  assert.match(theme, /transitionDuration:\s*'0\.01ms !important'/)
  assert.match(a11yCss, /^\.sr-only\s*\{/)
  assert.doesNotMatch(a11yCss, /focus-visible|prefers-reduced-motion|--nd-|\.nd-|\.auth-|\.operator-/)
})
