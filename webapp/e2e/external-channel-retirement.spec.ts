import { expect, test, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

test('canonical channels does not request or display retired external_channel surfaces', async ({ page }) => {
  const legacyRequests: string[] = []
  page.on('request', (request) => {
    const url = request.url().toLowerCase()
    if (url.includes('external-channel') || url.includes('external_channel')) legacyRequests.push(url)
  })

  await page.addInitScript(([key, token]) => sessionStorage.setItem(key, token), [TOKEN_KEY, 'operator-token'])
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, {
        id: 1,
        username: 'channel-manager',
        display_name: 'Channel Manager',
        role: 'admin',
        capabilities: ['channel_account.manage'],
      })
    }
    if (url.pathname === '/api/admin/channel-accounts') return json(route, [])
    return json(route, { detail: `Unexpected API ${url.pathname}` }, 404)
  })

  await page.goto('/channels')

  await expect(page.getByRole('heading', { level: 1, name: '渠道管理' })).toBeVisible()
  await expect(page.getByText(/ExternalChannel/i)).toHaveCount(0)
  await expect(page.locator('a[href*="external-channel"], a[href*="external_channel"]')).toHaveCount(0)
  expect(legacyRequests).toEqual([])
})
