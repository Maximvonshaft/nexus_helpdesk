import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')

const button = read('src/components/ui/Button.tsx')
const main = read('src/main.tsx')
const a11yCss = read('src/a11y.css')

test('Button defaults to type button while preserving explicit caller type', () => {
  assert.match(button, /type\s*=\s*'button'/)
  assert.match(button, /<button[\s\S]*type=\{type\}/)
  assert.doesNotMatch(button, /props as any/)
})

test('global accessibility stylesheet is loaded without runtime patchers', () => {
  assert.match(main, /import '@\/a11y\.css'/)
  assert.doesNotMatch(main, /a11yRuntime/)
  assert.doesNotMatch(main, /initA11yRuntimeRepair/)
})

test('global accessibility stylesheet provides focus and reduced-motion hardening', () => {
  assert.match(a11yCss, /\.sr-only/)
  assert.match(a11yCss, /button:focus-visible/)
  assert.match(a11yCss, /a\[href\]:focus-visible/)
  assert.match(a11yCss, /@media \(prefers-reduced-motion: reduce\)/)
  assert.match(a11yCss, /transform: none !important/)
})
