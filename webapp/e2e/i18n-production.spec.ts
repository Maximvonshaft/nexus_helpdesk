import { expect, test, type Page, type Route } from '@playwright/test'
import {
  TOKEN_KEY,
  fulfillResponsiveApi,
  json,
  responsiveUser,
} from './fixtures/responsiveConsole'

const LOCALE_KEY = 'nexus-operator-ui-locale'

async function setAnonymousLocale(page: Page, locale: 'en' | 'de') {
  await page.addInitScript(([key, value]) => localStorage.setItem(key, value), [LOCALE_KEY, locale])
}

async function mockAuthenticatedLocale(page: Page, initialLocale: 'en' | 'de') {
  let serverLocale: 'en' | 'de' = initialLocale
  let preferenceUpdates = 0
  await page.addInitScript(([tokenKey, localeKey, locale]) => {
    sessionStorage.setItem(tokenKey, 'i18n-production-token')
    localStorage.setItem(localeKey, locale)
  }, [TOKEN_KEY, LOCALE_KEY, initialLocale])

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, { ...responsiveUser, ui_locale: serverLocale })
    }
    if (url.pathname === '/api/auth/preferences' && route.request().method() === 'PATCH') {
      const payload = route.request().postDataJSON() as { ui_locale?: 'en' | 'de' }
      if (payload.ui_locale !== 'en' && payload.ui_locale !== 'de') {
        return json(route, { detail: 'invalid locale' }, 422)
      }
      serverLocale = payload.ui_locale
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
  }
}

test('English is complete on the unauthenticated entry surface', async ({ page }) => {
  await setAnonymousLocale(page, 'en')
  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Account', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Password', exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Interface language' })).toHaveValue('en')
  await expect(page.getByRole('main')).not.toContainText(/[\u3400-\u9fff]/u)
})

test('German entry copy is complete and does not overflow a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await setAnonymousLocale(page, 'de')
  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.getByRole('heading', { level: 1, name: 'Anmelden' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Konto', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Passwort', exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Oberflächensprache' })).toHaveValue('de')
  await expect(page.getByRole('main')).not.toContainText(/[\u3400-\u9fff]/u)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('authenticated language preference persists through the server and reloads the full UI', async ({ page }) => {
  const state = await mockAuthenticatedLocale(page, 'en')
  await page.goto('/account')

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('heading', { level: 1, name: 'Account settings' })).toBeVisible()
  const selector = page.getByRole('combobox', { name: 'Interface language' }).last()
  await selector.selectOption('de')

  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.getByRole('heading', { level: 1, name: 'Kontoeinstellungen' })).toBeVisible()
  await expect.poll(() => state.preferenceUpdates).toBe(1)
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), LOCALE_KEY)).toBe('de')
})
