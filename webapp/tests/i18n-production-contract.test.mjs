import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const entrypoint = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8')
const application = readFileSync(new URL('../src/application.tsx', import.meta.url), 'utf8')
const authenticatedPage = readFileSync(new URL('../src/app/AuthenticatedAppPage.tsx', import.meta.url), 'utf8')
const localeAuthority = readFileSync(new URL('../src/i18n/localeAuthority.ts', import.meta.url), 'utf8')
const runtime = readFileSync(new URL('../src/i18n/runtime.ts', import.meta.url), 'utf8')
const languageControl = readFileSync(new URL('../src/i18n/LanguageControl.tsx', import.meta.url), 'utf8')
const preferenceApi = readFileSync(new URL('../src/lib/uiPreferenceApi.ts', import.meta.url), 'utf8')
const accountPanel = readFileSync(new URL('../src/i18n/AccountLanguagePanel.tsx', import.meta.url), 'utf8')
const cnrMuiLocale = readFileSync(new URL('../src/i18n/cnrMuiLocale.ts', import.meta.url), 'utf8')
const themeProvider = readFileSync(new URL('../src/theme/NexusThemeProvider.tsx', import.meta.url), 'utf8')
const format = readFileSync(new URL('../src/lib/format.ts', import.meta.url), 'utf8')
const staticAssetLoader = readFileSync(new URL('../src/lib/staticJsonAssetRequest.ts', import.meta.url), 'utf8')
const httpTransport = readFileSync(new URL('../src/lib/httpTransport.ts', import.meta.url), 'utf8')

test('one synchronous i18next authority owns all production locales', () => {
  assert.match(runtime, /createInstance/)
  assert.match(localeAuthority, /enabledUiLocales = \['zh-CN', 'en', 'de', 'cnr'\]/)
  assert.match(localeAuthority, /sr-Latn-ME/)
  assert.match(entrypoint, /i18n\/\$\{catalogLocale\}\.json\?v=\$\{encodeURIComponent\(catalogSha\)\}/)
  assert.match(entrypoint, /Object\.keys\(value\)\.length === catalogMetadata\.inventory_messages/)
  assert.match(entrypoint, /catalogMetadata\.catalog_sha256\[catalogLocale\]/)
  assert.match(entrypoint, /await import\('\.\/application'\)/)
  assert.match(application, /I18nextProvider/)
  assert.match(runtime, /__NEXUS_UI_I18N_BOOTSTRAP__/)
  assert.match(runtime, /initAsync: false/)
  assert.match(runtime, /keySeparator: false/)
  assert.match(runtime, /nsSeparator: false/)
  assert.match(runtime, /synchronizeAuthenticatedUiLocale/)
})

test('catalog bootstrap cannot evaluate the translated API runtime early', () => {
  assert.match(entrypoint, /@\/lib\/staticJsonAssetRequest/)
  assert.doesNotMatch(entrypoint, /@\/lib\/apiClient/)
  assert.doesNotMatch(
    staticAssetLoader,
    /from\s+['"][^'"]*(?:i18n\/runtime|apiClient)['"]/u,
  )
  assert.match(staticAssetLoader, /fetchWithTimeout/)
  assert.match(httpTransport, /return await fetch\(/)
})

test('language changes persist through the authenticated server contract', () => {
  assert.match(preferenceApi, /PATCH/)
  assert.match(preferenceApi, /\/api\/auth\/preferences/)
  assert.match(languageControl, /uiPreferenceApi\.updateLocale/)
  assert.match(languageControl, /setUiLocale\(response\.ui_locale\)/)
  assert.match(languageControl, /result\.applied/)
  assert.match(languageControl, /Crnogorski/)
  assert.match(localeAuthority, /UI_LOCALE_TRANSITION_KEY/)
  assert.match(localeAuthority, /consumeUiLocaleTransition/)
  assert.match(localeAuthority, /sessionStorageAuthority\(\)\?\.getItem\(UI_LOCALE_STORAGE_KEY\)/)
  assert.match(localeAuthority, /persistence = 'session'/)
  assert.match(runtime, /stageUiLocaleTransition\(result\.locale\)/)
  assert.match(accountPanel, /Customer messages, ticket content and audit evidence are never translated/)
  assert.match(accountPanel, /Poruke korisnika, sadržaj tiketa i revizijski dokazi nikada se ne prevode/)
})

test('emergency recovery is verified and bound to the authenticated account', () => {
  assert.match(localeAuthority, /setRecoveryUiLocale\(value: unknown\): boolean/)
  assert.match(localeAuthority, /claimRecoveryUiLocale\(userIdValue: unknown\)/)
  assert.match(runtime, /synchronizeAuthenticatedUiLocale\(value: unknown, userId: unknown\)/)
  assert.match(authenticatedPage, /claimRecoveryUiLocale\(currentUser\.id\)/)
  assert.match(authenticatedPage, /synchronizeAuthenticatedUiLocale\(currentUser\.ui_locale, currentUser\.id\)/)
})

test('MUI and Intl formatting derive from the canonical UI locale', () => {
  assert.match(themeProvider, /deDE/)
  assert.match(themeProvider, /enUS/)
  assert.match(themeProvider, /cnrMuiLocale/)
  assert.match(themeProvider, /zhCN/)
  assert.match(cnrMuiLocale, /sljedeću stranicu/)
  assert.match(cnrMuiLocale, /posljednju stranicu/)
  assert.match(cnrMuiLocale, /Zvijezda/)
  assert.match(cnrMuiLocale, /sr-Latn-ME/)
  assert.match(format, /new Intl\.DateTimeFormat\(getIntlLocale\(\)/)
  assert.match(format, /new Intl\.NumberFormat\(getIntlLocale\(\)/)
})
