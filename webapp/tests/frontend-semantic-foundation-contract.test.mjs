import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const TEST_DIR = dirname(fileURLToPath(import.meta.url))
const WEBAPP_ROOT = resolve(TEST_DIR, '..')
const REPO_ROOT = resolve(WEBAPP_ROOT, '..')

const read = (relativePath) => readFileSync(join(REPO_ROOT, relativePath), 'utf8')

const paths = {
  main: 'webapp/src/main.tsx',
  tokens: 'webapp/src/styles/tokens.css',
  components: 'webapp/src/styles/components.css',
  legacy: 'webapp/src/styles.css',
  a11y: 'webapp/src/a11y.css',
  button: 'webapp/src/components/ui/Button.tsx',
  badge: 'webapp/src/components/ui/Badge.tsx',
  field: 'webapp/src/components/ui/Field.tsx',
  pageHeader: 'webapp/src/components/ui/PageHeader.tsx',
  confirmDialog: 'webapp/src/components/ui/ConfirmDialog.tsx',
  login: 'webapp/src/routes/login.tsx',
  smoke: 'webapp/e2e/smoke.spec.ts',
  product: 'webapp/PRODUCT.md',
  design: 'webapp/DESIGN.md',
}

function cssBlock(source, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 's'))?.[1] ?? ''
}

test('merged PRODUCT and DESIGN remain the implementation authority', () => {
  assert.match(read(paths.product), /case-resolution cockpit/)
  assert.match(read(paths.design), /Dense calm logistics cockpit/)
})

test('semantic component CSS loads after legacy compatibility CSS', () => {
  const source = read(paths.main)
  const legacyIndex = source.indexOf("import '@/styles.css'")
  const componentsIndex = source.indexOf("import '@/styles/components.css'")
  assert.ok(legacyIndex >= 0, 'legacy compatibility CSS import is missing')
  assert.ok(componentsIndex > legacyIndex, 'semantic component CSS must load after legacy compatibility CSS')
})

test('semantic tokens define the shared control and motion floor', () => {
  const source = read(paths.tokens)
  for (const token of [
    '--nd-control-height-sm',
    '--nd-control-height-md',
    '--nd-control-height-lg',
    '--nd-focus-ring',
    '--nd-motion-fast',
    '--nd-z-toast',
  ]) assert.ok(source.includes(token), `missing semantic token ${token}`)
  assert.match(source, /--nd-control-height-md:\s*44px/)
})

test('shared component stylesheet uses semantic tokens rather than raw colors', () => {
  const source = read(paths.components)
  assert.equal(/#[0-9a-f]{3,8}\b/i.test(source), false, 'shared component CSS must not contain raw hex colors')
  for (const selector of ['.nd-button', '.nd-badge', '.nd-field', '.nd-field-control']) {
    assert.ok(source.includes(selector), `missing shared selector ${selector}`)
  }
  for (const state of [':hover', ':focus-visible', ':active', ':disabled', '[aria-busy="true"]']) {
    assert.ok(source.includes(state), `missing interaction state ${state}`)
  }
})

test('Button exposes semantic size and loading behavior', () => {
  const source = read(paths.button)
  assert.match(source, /type Size = 'sm' \| 'md' \| 'lg'/)
  assert.match(source, /loading\?: boolean/)
  assert.match(source, /loadingLabel\?: string/)
  assert.ok(source.includes("'nd-button'"))
  assert.ok(source.includes("'aria-busy'"))
  assert.ok(source.includes('disabled={disabled || loading}'))
})

test('Badge exposes semantic tone classes', () => {
  const source = read(paths.badge)
  assert.ok(source.includes("'nd-badge'"))
  assert.match(source, /nd-badge--\$\{tone\}/)
})

test('Field uses explicit label association and semantic control classes', () => {
  const source = read(paths.field)
  assert.ok(source.includes('<label'))
  assert.ok(source.includes('htmlFor='))
  assert.equal(source.includes('<label className="field">'), false, 'field group must not wrap every child in one label')
  assert.ok(source.includes("'nd-field'"))
  assert.ok(source.includes("'nd-field-control'"))
  assert.ok(source.includes("'nd-input'"))
  assert.ok(source.includes("'nd-select'"))
  assert.ok(source.includes("'nd-textarea'"))
})

test('PageHeader supports a semantic heading level', () => {
  const source = read(paths.pageHeader)
  assert.match(source, /headingLevel\?: 1 \| 2 \| 3/)
  assert.match(source, /const Heading = `h\$\{headingLevel\}`/)
})

test('ConfirmDialog uses the shared Button for both actions', () => {
  const source = read(paths.confirmDialog)
  assert.equal(source.includes('<button ref={cancelRef} className="button"'), false)
  assert.match(source, /<Button ref=\{cancelRef\}/)
})

test('Login is a semantic keyboard-complete form', () => {
  const source = read(paths.login)
  assert.ok(source.includes('<main'))
  assert.ok(source.includes('<form'))
  assert.ok(source.includes('onSubmit={handleSubmit}'))
  assert.ok(source.includes('type="submit"'))
  assert.ok(source.includes('showPassword'))
  assert.ok(source.includes('aria-pressed={showPassword}'))
  assert.ok(source.includes('登录运营工作台'))
  assert.ok(source.includes('正在验证账号…'))
  assert.ok(source.includes('role="alert"'))
  assert.ok(source.includes('auth-context'))
  assert.ok(source.includes('事实'))
  assert.ok(source.includes('受控动作'))
  assert.ok(source.includes('安全结案'))
})

test('Login presentation removes the generic gradient and over-rounded glass card', () => {
  const source = read(paths.legacy)
  const shell = cssBlock(source, '.auth-shell')
  const card = cssBlock(source, '.auth-card')
  assert.equal(shell.includes('gradient'), false, 'Login shell must not use decorative gradients')
  assert.equal(card.includes('rgba('), false, 'Login task surface must not use translucent glass styling')
  assert.equal(/border-radius:\s*(2[0-9]|[3-9][0-9])px/.test(card), false, 'Login task surface is over-rounded')
  assert.ok(source.includes('.auth-context'))
  assert.ok(source.includes('.auth-sequence'))
})

test('reduced motion and Login browser expectations are explicit', () => {
  const a11y = read(paths.a11y)
  const smoke = read(paths.smoke)
  assert.match(a11y, /prefers-reduced-motion:\s*reduce/)
  for (const phrase of [
    'login form reveals the password and submits with Enter',
    '登录运营工作台',
    '显示密码',
    "setViewportSize({ width: 375, height: 812 })",
    'scrollWidth',
  ]) assert.ok(smoke.includes(phrase), `missing Login browser expectation: ${phrase}`)
})
