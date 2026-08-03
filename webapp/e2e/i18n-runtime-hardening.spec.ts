import { expect, test, type Page, type Route } from '@playwright/test'
import {
  TOKEN_KEY,
  fulfillResponsiveApi,
  json,
  responsiveUser,
} from './fixtures/responsiveConsole'

const LOCALE_KEY = 'nexus-operator-ui-locale'
const RECOVERY_KEY = 'nexus-operator-ui-locale-recovery'
const RECOVERY_FIXTURE_KEY = 'nexus-i18n-recovery-fixture-seeded'

test('a same-cardinality catalog with substituted bytes fails the release digest', async ({ page }) => {
  await page.addInitScript(([key, locale]) => localStorage.setItem(key, locale), [LOCALE_KEY, 'en'])
  await page.route('**/i18n/en.json*', async (route) => {
    const response = await route.fetch()
    const catalog = await response.json() as Record<string, string>
    const firstKey = Object.keys(catalog)[0]
    if (!firstKey) throw new Error('catalog_fixture_empty')
    catalog[firstKey] = `${catalog[firstKey]} tampered`
    await route.fulfill({
      response,
      contentType: 'application/json',
      body: JSON.stringify(catalog),
    })
  })

  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'blocked')
  await expect(page.getByRole('heading', {
    level: 1,
    name: 'The interface language could not be loaded',
  })).toBeVisible()
})

async function mockRecoveredAccount(page: Page) {
  let preferenceUpdates = 0
  let serverLocale = 'en'

  await page.addInitScript(([tokenKey, localeKey, recoveryKey]) => {
    sessionStorage.setItem(tokenKey, 'i18n-recovery-token')
    localStorage.setItem(localeKey, 'en')
    sessionStorage.setItem(recoveryKey, 'zh-CN')
  }, [TOKEN_KEY, LOCALE_KEY, RECOVERY_KEY])

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, {
        ...responsiveUser,
        ui_locale: serverLocale,
        ui_locale_configured: true,
      })
    }
    if (url.pathname === '/api/auth/preferences' && route.request().method() === 'PATCH') {
      const payload = route.request().postDataJSON() as { ui_locale?: string }
      serverLocale = payload.ui_locale ?? serverLocale
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

test('temporary Chinese recovery never becomes an implicit account preference', async ({ page }) => {
  const state = await mockRecoveredAccount(page)

  await page.goto('/account')

  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await expect(page.getByRole('heading', { level: 1, name: '账户设置' })).toBeVisible()
  await expect(page.getByRole('button', { name: '将当前中文保存到账户' })).toBeVisible()
  await expect.poll(() => state.preferenceUpdates).toBe(0)
  await expect.poll(() => state.serverLocale).toBe('en')
  await expect.poll(async () => page.evaluate((key) => {
    const raw = sessionStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  }, RECOVERY_KEY)).toEqual({ locale: 'zh-CN', userId: responsiveUser.id })
})

test('the operator can explicitly persist the active recovery language', async ({ page }) => {
  const state = await mockRecoveredAccount(page)

  await page.goto('/account')
  await page.getByRole('button', { name: '将当前中文保存到账户' }).click()

  await expect.poll(() => state.preferenceUpdates).toBe(1)
  await expect.poll(() => state.serverLocale).toBe('zh-CN')
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), RECOVERY_KEY)).toBeNull()
  await expect(page.getByText('中文已保存为账户界面语言。')).toBeVisible()
})

test('catalog recovery does not reload when session storage rejects the recovery state', async ({ page }) => {
  let documentRequests = 0
  page.on('request', (request) => {
    if (request.resourceType() === 'document') documentRequests += 1
  })

  await page.addInitScript(([localeKey, recoveryKey]) => {
    const sessionAuthority = window.sessionStorage
    const nativeSetItem = Storage.prototype.setItem
    nativeSetItem.call(window.localStorage, localeKey, 'de')
    Storage.prototype.setItem = function setItem(key: string, value: string) {
      if (this === sessionAuthority && key === recoveryKey) {
        throw new DOMException('blocked', 'SecurityError')
      }
      return nativeSetItem.call(this, key, value)
    }
  }, [LOCALE_KEY, RECOVERY_KEY])
  await page.route('**/i18n/de.json*', (route) => route.fulfill({ status: 503, body: 'unavailable' }))

  await page.goto('/login')
  await page.getByRole('button', { name: 'Vereinfachtes Chinesisch verwenden' }).click()

  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'blocked')
  await expect(page.getByRole('alert')).toContainText('Der Browser konnte die Wiederherstellungssprache nicht speichern.')
  await expect.poll(() => documentRequests).toBe(1)
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), RECOVERY_KEY)).toBeNull()
})

test('recovery state is cleared when a different account takes over the same tab', async ({ page }) => {
  let currentUser = {
    ...responsiveUser,
    id: 701,
    username: 'recovery-owner',
    display_name: 'Recovery Owner',
    ui_locale: 'en',
    ui_locale_configured: true,
  }
  let accountDocuments = 0
  page.on('request', (request) => {
    if (request.resourceType() !== 'document') return
    if (new URL(request.url()).pathname === '/account') accountDocuments += 1
  })

  await page.addInitScript(([tokenKey, localeKey, recoveryKey, fixtureKey]) => {
    sessionStorage.setItem(tokenKey, 'i18n-account-scope-token')
    if (sessionStorage.getItem(fixtureKey)) return
    localStorage.setItem(localeKey, 'en')
    sessionStorage.setItem(recoveryKey, 'zh-CN')
    sessionStorage.setItem(fixtureKey, 'true')
  }, [TOKEN_KEY, LOCALE_KEY, RECOVERY_KEY, RECOVERY_FIXTURE_KEY])

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, currentUser)
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

  await page.goto('/account')
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await expect.poll(async () => page.evaluate((key) => {
    const raw = sessionStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  }, RECOVERY_KEY)).toEqual({ locale: 'zh-CN', userId: 701 })

  currentUser = {
    ...responsiveUser,
    id: 702,
    username: 'next-operator',
    display_name: 'Next Operator',
    ui_locale: 'de',
    ui_locale_configured: true,
  }
  await page.reload()

  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'loaded')
  await expect(page.getByRole('heading', { level: 1, name: 'Kontoeinstellungen' })).toBeVisible()
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), RECOVERY_KEY)).toBeNull()
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), LOCALE_KEY)).toBe('de')
  await page.waitForTimeout(300)
  expect(accountDocuments).toBeLessThanOrEqual(3)
})

test('an unconfigured account does not inherit another account recovery locale', async ({ page }) => {
  let currentUser = {
    ...responsiveUser,
    id: 711,
    username: 'recovery-owner-unconfigured-handoff',
    display_name: 'Recovery Owner',
    ui_locale: 'en',
    ui_locale_configured: true,
  }
  const preferenceUpdates: string[] = []
  let accountDocuments = 0
  page.on('request', (request) => {
    if (request.resourceType() !== 'document') return
    if (new URL(request.url()).pathname === '/account') accountDocuments += 1
  })

  await page.addInitScript(([tokenKey, localeKey, recoveryKey, fixtureKey]) => {
    sessionStorage.setItem(tokenKey, 'i18n-unconfigured-account-scope-token')
    if (sessionStorage.getItem(fixtureKey)) return
    localStorage.setItem(localeKey, 'en')
    sessionStorage.setItem(recoveryKey, 'zh-CN')
    sessionStorage.setItem(fixtureKey, 'true')
  }, [TOKEN_KEY, LOCALE_KEY, RECOVERY_KEY, RECOVERY_FIXTURE_KEY])

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, currentUser)
    if (url.pathname === '/api/auth/preferences' && route.request().method() === 'PATCH') {
      const payload = route.request().postDataJSON() as { ui_locale?: string }
      const nextLocale = payload.ui_locale ?? currentUser.ui_locale
      preferenceUpdates.push(nextLocale)
      currentUser = {
        ...currentUser,
        ui_locale: nextLocale,
        ui_locale_configured: true,
      }
      return json(route, { ui_locale: nextLocale })
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

  await page.goto('/account')
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await expect.poll(async () => page.evaluate((key) => {
    const raw = sessionStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  }, RECOVERY_KEY)).toEqual({ locale: 'zh-CN', userId: 711 })

  currentUser = {
    ...responsiveUser,
    id: 712,
    username: 'unconfigured-next-operator',
    display_name: 'Unconfigured Next Operator',
    ui_locale: 'zh-CN',
    ui_locale_configured: false,
  }
  await page.reload()

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'loaded')
  await expect(page.getByRole('heading', { level: 1, name: 'Account settings' })).toBeVisible()
  await expect.poll(() => preferenceUpdates).toEqual(['en'])
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), RECOVERY_KEY)).toBeNull()
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), LOCALE_KEY)).toBe('en')
  await page.waitForTimeout(300)
  expect(accountDocuments).toBeLessThanOrEqual(3)
})

test('authenticated locale adoption uses session fallback when local storage writes are blocked', async ({ page }) => {
  let accountDocuments = 0
  page.on('request', (request) => {
    if (request.resourceType() !== 'document') return
    if (new URL(request.url()).pathname === '/account') accountDocuments += 1
  })

  await page.addInitScript(([tokenKey, localeKey]) => {
    sessionStorage.setItem(tokenKey, 'i18n-session-fallback-token')
    if (!sessionStorage.getItem(localeKey)) sessionStorage.setItem(localeKey, 'en')
    const durableStorage = window.localStorage
    const nativeSetItem = Storage.prototype.setItem
    Storage.prototype.setItem = function setItem(key: string, value: string) {
      if (this === durableStorage) throw new DOMException('blocked', 'SecurityError')
      return nativeSetItem.call(this, key, value)
    }
  }, [TOKEN_KEY, LOCALE_KEY])

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, {
        ...responsiveUser,
        ui_locale: 'de',
        ui_locale_configured: true,
      })
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

  await page.goto('/account')

  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'loaded')
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), LOCALE_KEY)).toBe('de')
  await page.waitForTimeout(300)
  expect(accountDocuments).toBe(2)
})
