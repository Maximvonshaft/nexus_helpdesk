import { expect, test } from '@playwright/test'

const LOCALE_KEY = 'nexus-operator-ui-locale'

async function selectEnglish(page) {
  await page.addInitScript(([key, locale]) => localStorage.setItem(key, locale), [LOCALE_KEY, 'en'])
}

test('catalog request is versioned by an immutable SHA-256 digest', async ({ page }) => {
  await selectEnglish(page)
  const catalogRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return url.pathname.endsWith('/i18n/en.json')
  })

  await page.goto('/login')
  const request = await catalogRequest
  const version = new URL(request.url()).searchParams.get('v')

  expect(version).toMatch(/^[0-9a-f]{64}$/)
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'loaded')
})

test('an HTTP 200 partial catalog still blocks mixed-language startup', async ({ page }) => {
  await selectEnglish(page)
  await page.route('**/i18n/en.json*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ incomplete: 'Incomplete' }),
  }))

  await page.goto('/login')

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-ui-catalog', 'blocked')
  await expect(page.getByRole('heading', {
    level: 1,
    name: 'The interface language could not be loaded',
  })).toBeVisible()
  await expect(page.getByRole('main')).not.toContainText(/[\u3400-\u9fff]/u)
})
