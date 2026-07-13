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
const tokens = read('src/styles/tokens.css')
const components = read('src/styles/components.css')
const a11y = read('src/a11y.css')
const authPath = resolve(root, 'src/styles/auth.css')
const auth = existsSync(authPath) ? read('src/styles/auth.css') : ''


test('semantic CSS authorities load after bounded legacy compatibility', () => {
  const tokenIndex = main.indexOf("@/styles/tokens.css")
  const legacyIndex = main.indexOf("@/styles.css")
  const componentIndex = main.indexOf("@/styles/components.css")
  const authIndex = main.indexOf("@/styles/auth.css")

  assert.ok(tokenIndex >= 0)
  assert.ok(legacyIndex > tokenIndex)
  assert.ok(componentIndex > legacyIndex)
  assert.ok(authIndex > componentIndex)
})


test('shared Button and Badge expose semantic authority with bounded compatibility', () => {
  assert.match(button, /nd-button/)
  assert.match(button, /loadingLabel/)
  assert.match(button, /aria-busy/)
  assert.match(button, /size/)
  assert.match(button, /loading \? true/)
  assert.match(badge, /nd-badge/)
  assert.match(badge, /nd-badge--/)
})


test('Field uses explicit label association and semantic controls', () => {
  assert.match(field, /<label[^>]*htmlFor=/)
  assert.match(field, /nd-field/)
  assert.match(field, /nd-control/)
  assert.doesNotMatch(field, /<label className="field">/)
  assert.match(field, /aria-describedby/)
  assert.match(field, /role="alert"/)
})


test('PageHeader and ConfirmDialog consume shared semantic primitives', () => {
  assert.match(pageHeader, /headingLevel/)
  assert.match(pageHeader, /const Heading/)
  assert.match(confirmDialog, /@radix-ui\/react-dialog/)
  assert.match(confirmDialog, /Dialog\.Title/)
  assert.match(confirmDialog, /Dialog\.Description/)
  assert.match(confirmDialog, /Dialog\.Close asChild/)
  assert.match(confirmDialog, /loading=\{busy\}/)
  assert.doesNotMatch(confirmDialog, /处理中\.\.\./)
})


test('Login is one semantic keyboard-complete authentication flow', () => {
  assert.match(login, /<main className="auth-shell">/)
  assert.match(login, /<form[^>]*onSubmit=/)
  assert.match(login, /type="submit"/)
  assert.match(login, /aria-pressed=\{showPassword\}/)
  assert.match(login, /role="alert"/)
  assert.match(login, /无法登录。请检查账号和密码后重试。/)
  assert.match(login, /useState\(''\)/)
  assert.doesNotMatch(login, /useState\('admin'\)/)
  assert.doesNotMatch(login, /navigate\(\{ to: '\/webchat'/)
  assert.equal((login.match(/navigate\(\{ to: '\/'/g) ?? []).length, 2)
})


test('semantic tokens and component CSS encode interaction states without raw feature colors', () => {
  assert.match(tokens, /--nd-control-height-md:\s*44px/)
  assert.match(tokens, /--nd-focus-ring:/)
  assert.match(tokens, /--nd-motion-fast:/)
  assert.match(tokens, /--nd-z-dialog:/)
  assert.match(components, /min-height:\s*var\(--nd-control-height-md\)/)
  assert.match(components, /\.nd-button:focus-visible/)
  assert.match(components, /\.nd-button\[aria-busy="true"\]/)
  assert.doesNotMatch(components, /#[0-9a-f]{3,8}\b/i)
  assert.doesNotMatch(components, /rgba?\(/i)
})


test('Login presentation is semantic, restrained, responsive, and reduced-motion safe', () => {
  assert.equal(existsSync(authPath), true, 'src/styles/auth.css must exist')
  assert.match(auth, /var\(--nd-/)
  assert.match(auth, /@media \(max-width:/)
  assert.match(auth, /100dvh/)
  assert.doesNotMatch(auth, /gradient/i)
  assert.doesNotMatch(auth, /rgba?\(/i)
  assert.doesNotMatch(auth, /#[0-9a-f]{3,8}\b/i)
  assert.doesNotMatch(auth, /border-radius:\s*(?:1[3-9]|[2-9]\d)px/)
  assert.match(a11y, /prefers-reduced-motion/)
  assert.match(a11y, /\.nd-button/)
  assert.match(a11y, /\.auth-/)
})


test('dedicated Login browser evidence exists', () => {
  assert.equal(existsSync(resolve(root, 'e2e/login-semantic.spec.ts')), true)
})
