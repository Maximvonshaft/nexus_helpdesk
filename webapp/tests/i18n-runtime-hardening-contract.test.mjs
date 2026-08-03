import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const WEBAPP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const read = (path) => readFileSync(resolve(WEBAPP_ROOT, path), 'utf8')

const apiClient = read('src/lib/apiClient.ts')
const staticAssetLoader = read('src/lib/staticJsonAssetRequest.ts')
const httpTransport = read('src/lib/httpTransport.ts')
const main = read('src/main.tsx')
const authenticatedPage = read('src/app/AuthenticatedAppPage.tsx')
const accountLanguagePanel = read('src/i18n/AccountLanguagePanel.tsx')
const catalogLoadFailure = read('src/i18n/catalogLoadFailure.ts')
const localeAuthority = read('src/i18n/localeAuthority.ts')
const runtime = read('src/i18n/runtime.ts')
const browserContract = read('e2e/i18n-runtime-hardening.spec.ts')

test('catalog bytes are authenticated through the bootstrap-safe HTTP authorities', () => {
  assert.match(staticAssetLoader, /expectedSha256\?: string/)
  assert.match(staticAssetLoader, /subtle\.digest\('SHA-256', bytes\)/)
  assert.match(staticAssetLoader, /static_asset_digest_mismatch/)
  assert.match(staticAssetLoader, /new TextDecoder\('utf-8', \{ fatal: true \}\)/)
  assert.match(staticAssetLoader, /fetchWithTimeout/)
  assert.match(httpTransport, /return await fetch\(/)
  assert.doesNotMatch(staticAssetLoader, /mapApiErrorMessage|AuthExpiredError|Authorization/)
  assert.match(apiClient, /fetchWithTimeout/)
  assert.match(main, /expectedSha256: catalogSha/)
})

test('session recovery cannot be promoted to account authority implicitly', () => {
  assert.match(authenticatedPage, /claimRecoveryUiLocale\(currentUser\.id\)/)
  assert.match(authenticatedPage, /const pendingRecovery = readRecoveryUiLocale\(\)/)
  assert.match(authenticatedPage, /resolvePreferredUiLocale\(\)/)
  assert.match(authenticatedPage, /setUiLocale\(preferredLocale\)/)
  const recoveryGuard = authenticatedPage.indexOf('if (claimRecoveryUiLocale(currentUser.id)) return')
  const persistedUpdate = authenticatedPage.indexOf('uiPreferenceApi.updateLocale(selectedLocale)')
  assert.ok(recoveryGuard >= 0, 'account-scoped recovery guard must exist')
  assert.ok(persistedUpdate > recoveryGuard, 'recovery guard must run before persistence')
})

test('recovery state is verified and scoped to one authenticated account', () => {
  assert.match(localeAuthority, /type UiLocaleRecoveryState/)
  assert.match(localeAuthority, /userId: number \| null/)
  assert.match(localeAuthority, /writeRecoveryState\(state: UiLocaleRecoveryState\): boolean/)
  assert.match(localeAuthority, /verified\?\.locale === state\.locale && verified\.userId === state\.userId/)
  assert.match(localeAuthority, /claimRecoveryUiLocale\(userIdValue: unknown\)/)
  assert.match(localeAuthority, /if \(state\.userId !== null\) \{\s*clearRecoveryUiLocale\(\)/s)
  assert.match(runtime, /synchronizeAuthenticatedUiLocale\(value: unknown, userId: unknown\)/)
  assert.match(runtime, /claimRecoveryUiLocale\(userId\)/)
})

test('catalog recovery reloads only after the recovery state is retained', () => {
  const verifiedWrite = catalogLoadFailure.indexOf("if (!setRecoveryUiLocale('zh-CN'))")
  const reload = catalogLoadFailure.indexOf('window.location.reload()', verifiedWrite)
  assert.ok(verifiedWrite >= 0, 'catalog recovery must verify session persistence')
  assert.ok(reload > verifiedWrite, 'reload must happen after verified recovery persistence')
  assert.match(catalogLoadFailure, /status\.setAttribute\('role', 'alert'\)/)
  assert.match(catalogLoadFailure, /recoveryUnavailable/)
})

test('recovery persistence requires an explicit account action', () => {
  assert.match(accountLanguagePanel, /readRecoveryUiLocale/)
  assert.match(accountLanguagePanel, /uiPreferenceApi\.updateLocale\(activeLocale\)/)
  assert.match(accountLanguagePanel, /setUiLocale\(response\.ui_locale, \{ reload: false \}\)/)
  assert.match(accountLanguagePanel, /将当前中文保存到账户/)
})

test('browser acceptance covers substituted bytes, recovery and storage failure', () => {
  assert.match(browserContract, /same-cardinality catalog with substituted bytes/)
  assert.match(browserContract, /\*\*\/i18n\/en\.json\*/)
  assert.match(browserContract, /data-ui-catalog', 'blocked'/)
  assert.match(browserContract, /temporary Chinese recovery never becomes an implicit account preference/)
  assert.match(browserContract, /state\.preferenceUpdates\)\.toBe\(0\)/)
  assert.match(browserContract, /operator can explicitly persist the active recovery language/)
  assert.match(browserContract, /state\.preferenceUpdates\)\.toBe\(1\)/)
  assert.match(browserContract, /session storage rejects the recovery state/)
  assert.match(browserContract, /documentRequests\)\.toBe\(1\)/)
  assert.match(browserContract, /different account takes over the same tab/)
  assert.match(browserContract, /userId: 701/)
  assert.match(browserContract, /unconfigured account does not inherit another account recovery locale/)
  assert.match(browserContract, /preferenceUpdates\)\.toEqual\(\['en'\]\)/)
  assert.match(browserContract, /local storage writes are blocked/)
  assert.match(browserContract, /accountDocuments\)\.toBe\(2\)/)
})
