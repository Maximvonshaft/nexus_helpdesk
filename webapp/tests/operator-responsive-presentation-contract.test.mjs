import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const presentation = readFileSync(resolve(root, 'src/app/OperatorPresentation.tsx'), 'utf8')

test('shared operator headings stack before controls collide', () => {
  assert.match(presentation, /direction=\{\{ xs: 'column', sm: 'row' \}\}/)
  assert.match(presentation, /alignItems: \{ xs: 'stretch', sm: 'flex-start' \}/)
  assert.match(presentation, /overflowWrap: 'anywhere'/)
})

test('fact grids use a bounded tablet density before full desktop density', () => {
  assert.match(presentation, /const tabletColumns = Math\.min\(3, Math\.max\(1, columns\)\)/)
  assert.match(presentation, /md: `repeat\(\$\{tabletColumns\}/)
  assert.match(presentation, /lg: `repeat\(\$\{desktopColumns\}/)
})

test('shared empty, loading and error states remain usable on narrow screens', () => {
  assert.match(presentation, /width: \{ xs: '100%', sm: 'auto' \}/)
  assert.match(presentation, /textAlign: 'center'/)
  assert.match(presentation, /sx=\{\{ overflowWrap: 'anywhere' \}\}/)
})
