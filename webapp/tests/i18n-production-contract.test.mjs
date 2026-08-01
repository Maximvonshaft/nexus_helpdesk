import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const entrypoint = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8')
const application = readFileSync(new URL('../src/application.tsx', import.meta.url), 'utf8')
const localeAuthority = readFileSync(new URL('../src/i18n/localeAuthority.ts', import.meta.url), 'utf8')
const runtime = readFileSync(new URL('../src/i18n/runtime.ts', import.meta.url), 'utf8')
const languageControl = readFileSync(new URL('../src/i18n/LanguageControl.tsx', import.meta.url), 'utf8')
const preferenceApi = readFileSync(new URL('../src/lib/uiPreferenceApi.ts', import.meta.url), 'utf8')
const accountPanel = readFileSync(new URL('../src/i18n/AccountLanguagePanel.tsx', import.meta.url), 'utf8')
const themeProvider = readFileSync(new URL('../src/theme/NexusThemeProvider.tsx', import.meta.url), 'utf8')
const format = readFileSync(new URL('../src/lib/format.ts', import.meta.url), 'utf8')

test('one synchronous i18next authority owns all production locales', () => {
  assert.match(runtime, /createInstance/)
  assert.match(localeAuthority, /enabledUiLocales = \['zh-CN', 'en', 'de'\]/)
  assert.match(entrypoint, /i18n\/\$\{locale\}\.json/)
  assert.match(entrypoint, /await import\('\.\/application'\)/)
  assert.match(application, /I18nextProvider/)
  assert.match(runtime, /__NEXUS_UI_I18N_BOOTSTRAP__/)
  assert.match(runtime, /initImmediate: false/)
  assert.match(runtime, /keySeparator: false/)
  assert.match(runtime, /nsSeparator: false/)
  assert.match(runtime, /synchronizeAuthenticatedUiLocale/)
})

test('language changes persist through the authenticated server contract', () => {
  assert.match(preferenceApi, /PATCH/)
  assert.match(preferenceApi, /\/api\/auth\/preferences/)
  assert.match(languageControl, /uiPreferenceApi\.updateLocale/)
  assert.match(languageControl, /setUiLocale\(response\.ui_locale\)/)
  assert.match(accountPanel, /Customer messages, ticket content and audit evidence are never translated/)
})

test('MUI and Intl formatting derive from the canonical UI locale', () => {
  assert.match(themeProvider, /deDE/)
  assert.match(themeProvider, /enUS/)
  assert.match(themeProvider, /zhCN/)
  assert.match(format, /new Intl\.DateTimeFormat\(getIntlLocale\(\)/)
  assert.match(format, /new Intl\.NumberFormat\(getIntlLocale\(\)/)
})
