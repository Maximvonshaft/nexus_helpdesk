import { expect, test, type Page, type Route } from '@playwright/test'
import {
  TOKEN_KEY,
  fulfillResponsiveApi,
  json,
  responsiveUser,
} from './fixtures/responsiveConsole'

const LOCALE_KEY = 'nexus-operator-ui-locale'
type TestLocale = 'zh-CN' | 'en' | 'de' | 'cnr'

type RuntimeDiagnostics = {
  pageErrors: string[]
  consoleErrors: string[]
  documents: string[]
}

function captureRuntimeDiagnostics(page: Page): RuntimeDiagnostics {
  const diagnostics: RuntimeDiagnostics = { pageErrors: [], consoleErrors: [], documents: [] }
  page.on('pageerror', (error) => diagnostics.pageErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') diagnostics.consoleErrors.push(message.text())
  })
  page.on('request', (request) => {
    if (request.resourceType() === 'document') diagnostics.documents.push(request.url())
  })
  return diagnostics
}

async function expectLocalizedHeading(
  page: Page,
  name: string,
  diagnostics: RuntimeDiagnostics,
) {
  try {
    await page.getByRole('heading', { level: 1, name }).waitFor({ state: 'visible', timeout: 10_000 })
  } catch (error) {
    throw new Error(JSON.stringify({
      error: 'localized_heading_missing',
      expected: name,
      url: page.url(),
      lang: await page.locator('html').getAttribute('lang'),
      catalog: await page.locator('html').getAttribute('data-ui-catalog'),
      body: (await page.locator('body').innerText()).slice(0, 4000),
      ...diagnostics,
      cause: error instanceof Error ? error.message : String(error),
    }))
  }
}

async function setAnonymousLocale(page: Page, locale: Exclude<TestLocale, 'zh-CN'>) {
  await page.addInitScript(([key, value]) => localStorage.setItem(key, value), [LOCALE_KEY, locale])
}

async function mockAuthenticatedLocale(
  page: Page,
  initialLocale: TestLocale,
  configured = true,
  deviceLocale: TestLocale = initialLocale,
) {
  let serverLocale: TestLocale = initialLocale
  let serverConfigured = configured
  let preferenceUpdates = 0
  await page.addInitScript(([tokenKey, localeKey, locale]) => {
    sessionStorage.setItem(tokenKey, 'i18n-production-token')
    localStorage.setItem(localeKey, locale)
  }, [TOKEN_KEY, LOCALE_KEY, deviceLocale])

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, {
        ...responsiveUser,
        ui_locale: serverLocale,
        ui_locale_configured: serverConfigured,
      })
    }
    if (url.pathname === '/api/auth/preferences' && route.request().method() === 'PATCH') {
      const payload = route.request().postDataJSON() as { ui_locale?: TestLocale }
      if (!payload.ui_locale || !['zh-CN', 'en', 'de', 'cnr'].includes(payload.ui_locale)) {
        return json(route, { detail: 'invalid locale' }, 422)
      }
      serverLocale = payload.ui_locale
      serverConfigured = true
      preferenceUpdates += 1
      return json(route, { ui_locale: serverLocale })
    }
    if (url.pathname === '/api/auth/mfa/status') {
      return json(route, {
        enabled: false,
        setup_pending: false,
        confirmed_at: null,
        last_verified_at: null,
        recovery_codes_remaining: 0,
      })
    }
    return fulfillResponsiveApi(route)
  })

  return {
    get preferenceUpdates() {
      return preferenceUpdates
    },
    get serverLocale() {
      return serverLocale
    },
  }
}

test('English is complete on the unauthenticated entry surface', async ({ page }) => {
  await setAnonymousLocale(page, 'en')
  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'loaded')
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Account', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Password', exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Interface language' })).toContainText('English')
  await expect(page.getByRole('main')).not.toContainText(/[\u3400-\u9fff]/u)
})

test('German entry copy is complete and does not overflow a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await setAnonymousLocale(page, 'de')
  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'loaded')
  await expect(page.getByRole('heading', { level: 1, name: 'Anmelden' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Konto', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Passwort', exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Oberflächensprache' })).toContainText('Deutsch')
  await expect(page.getByRole('main')).not.toContainText(/[\u3400-\u9fff]/u)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('Montenegrin entry copy is complete and does not overflow a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await setAnonymousLocale(page, 'cnr')
  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'cnr')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'loaded')
  await expect(page.getByRole('heading', { level: 1, name: 'Prijava' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Nalog', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Lozinka', exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Jezik interfejsa' })).toContainText('Crnogorski')
  await expect(page.getByRole('main')).not.toContainText(/[\u3400-\u9fff]/u)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('an unconfigured account adopts the current device locale exactly once', async ({ page }) => {
  const state = await mockAuthenticatedLocale(page, 'zh-CN', false, 'en')
  await page.goto('/account')

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('heading', { level: 1, name: 'Account settings' })).toBeVisible()
  await expect.poll(() => state.preferenceUpdates).toBe(1)
  await expect.poll(() => state.serverLocale).toBe('en')

  await page.reload()
  await expect(page.getByRole('heading', { level: 1, name: 'Account settings' })).toBeVisible()
  await expect.poll(() => state.preferenceUpdates).toBe(1)
})

test('authenticated German preference persists through the server and reloads the full UI', async ({ page }) => {
  const diagnostics = captureRuntimeDiagnostics(page)
  const state = await mockAuthenticatedLocale(page, 'en')
  await page.goto('/account')

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('heading', { level: 1, name: 'Account settings' })).toBeVisible()
  const selector = page.getByRole('combobox', { name: 'Interface language' }).last()
  await selector.click()
  await page.getByRole('option', { name: 'Deutsch' }).click()

  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expectLocalizedHeading(page, 'Kontoeinstellungen', diagnostics)
  await expect.poll(() => state.preferenceUpdates).toBe(1)
  await expect.poll(() => state.serverLocale).toBe('de')
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), LOCALE_KEY)).toBe('de')
})

test('authenticated Montenegrin preference persists through the server and reloads the full UI', async ({ page }) => {
  const diagnostics = captureRuntimeDiagnostics(page)
  const state = await mockAuthenticatedLocale(page, 'en')
  await page.goto('/account')

  const selector = page.getByRole('combobox', { name: 'Interface language' }).last()
  await selector.click()
  await page.getByRole('option', { name: 'Crnogorski' }).click()

  await expect(page.locator('html')).toHaveAttribute('lang', 'cnr')
  await expectLocalizedHeading(page, 'Podešavanja naloga', diagnostics)
  await expect.poll(() => state.preferenceUpdates).toBe(1)
  await expect.poll(() => state.serverLocale).toBe('cnr')
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), LOCALE_KEY)).toBe('cnr')
})

test('a missing German catalog blocks mixed-language startup and offers safe recovery', async ({ page }) => {
  await setAnonymousLocale(page, 'de')
  await page.route('**/i18n/de.json*', (route) => route.fulfill({ status: 503, body: 'unavailable' }))
  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'blocked')
  await expect(page.getByRole('heading', { level: 1, name: 'Die Oberflächensprache konnte nicht geladen werden' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Erneut versuchen' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Vereinfachtes Chinesisch verwenden' })).toBeVisible()
  await expect(page.getByRole('main')).not.toContainText(/[\u3400-\u9fff]/u)

  await page.getByRole('button', { name: 'Vereinfachtes Chinesisch verwenden' }).click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await expect(page.getByRole('heading', { level: 1, name: '登录' })).toBeVisible()
})

test('a missing Montenegrin catalog blocks mixed-language startup and offers safe recovery', async ({ page }) => {
  await setAnonymousLocale(page, 'cnr')
  await page.route('**/i18n/cnr.json*', (route) => route.fulfill({ status: 503, body: 'unavailable' }))
  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'cnr')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'blocked')
  await expect(page.getByRole('heading', { level: 1, name: 'Jezik interfejsa nije moguće učitati' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Pokušaj ponovo' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Koristi pojednostavljeni kineski' })).toBeVisible()
  await expect(page.getByRole('main')).not.toContainText(/[\u3400-\u9fff]/u)
})
