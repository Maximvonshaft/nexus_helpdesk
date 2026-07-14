import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(process.cwd())
const read = (path) => readFileSync(resolve(root, path), 'utf8')
const main = read('src/main.tsx')
const button = read('src/components/ui/Button.tsx')
const badge = read('src/components/ui/Badge.tsx')
const field = read('src/components/ui/Field.tsx')
const pageHeader = read('src/components/ui/PageHeader.tsx')
const confirmDialog = read('src/components/ui/ConfirmDialog.tsx')
const login = read('src/routes/login.tsx')
const components = read('src/styles/components.css')
const auth = read('src/styles/auth.css')
const a11y = read('src/a11y.css')

test('semantic authorities load in one deterministic order', () => {
  const paths = ['@/styles/tokens.css', '@/styles.css', '@/a11y.css', '@/styles/components.css', '@/styles/auth.css', '@/styles/service-shell.css']
  const indexes = paths.map((path) => main.indexOf(path))
  assert.ok(indexes.every((value) => value >= 0))
  assert.deepEqual([...indexes].sort((a, b) => a - b), indexes)
})

test('shared primitives expose only the nd component vocabulary', () => {
  assert.match(button, /nd-button/)
  assert.doesNotMatch(button, /className=\{?['"]button(?:\s|['"])/)
  assert.match(badge, /nd-badge/)
  assert.doesNotMatch(badge, /className=\{?['"]badge(?:\s|['"])/)
  assert.match(field, /nd-field/)
  assert.match(field, /nd-control/)
  assert.doesNotMatch(field, /className=\{?['"](?:field|input|select|textarea)(?:\s|['"])/)
  assert.match(pageHeader, /nd-page-header/)
  assert.doesNotMatch(pageHeader, /page-header nd-page-header/)
})

test('forms and dialogs remain accessible and explicit', () => {
  assert.match(field, /htmlFor=/)
  assert.match(field, /aria-describedby/)
  assert.match(field, /role="alert"/)
  assert.match(confirmDialog, /@radix-ui\/react-dialog/)
  assert.match(confirmDialog, /Dialog\.Title/)
  assert.match(confirmDialog, /Dialog\.Description/)
  assert.match(confirmDialog, /Dialog\.Close asChild/)
})

test('login is customer-service branded and contains no internal automation language', () => {
  assert.match(login, /Nexus Customer Service/)
  assert.match(login, /把客户问题处理到结果/)
  assert.match(login, /type="submit"/)
  assert.match(login, /aria-pressed=\{showPassword\}/)
  assert.doesNotMatch(login, /\b(?:AI|Runtime|Provider|RAG|Prompt|Model|Agent)\b/i)
})

test('component and authentication styles consume tokens only', () => {
  for (const source of [components, auth]) {
    assert.match(source, /var\(--nd-/)
    assert.doesNotMatch(source, /#[0-9a-f]{3,8}\b/i)
    assert.doesNotMatch(source, /rgba?\(/i)
  }
  assert.match(a11y, /prefers-reduced-motion/)
  assert.match(a11y, /button:focus-visible/)
  assert.equal(existsSync(resolve(root, 'e2e/login-semantic.spec.ts')), true)
})
