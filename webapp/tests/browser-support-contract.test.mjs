import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const support = fs.readFileSync(new URL('../BROWSER_SUPPORT.md', import.meta.url), 'utf8')
const playwright = fs.readFileSync(new URL('../playwright.config.ts', import.meta.url), 'utf8')

test('operator browser support is explicit and never silently claims untested engines', () => {
  assert.match(support, /Playwright-pinned Chromium|Playwright version/)
  assert.match(support, /Firefox and WebKit\/Safari are not represented as certified/)
  assert.match(support, /public WebChat widget/i)
  assert.match(playwright, /headless: true/)
})
