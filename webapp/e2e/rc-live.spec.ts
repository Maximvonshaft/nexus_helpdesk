import { expect, test } from '@playwright/test'

const adminUsername = process.env.RC_TEST_ADMIN_USERNAME || ''
const adminPassword = process.env.RC_TEST_ADMIN_PASSWORD || ''
const sourceSha = process.env.RC_SOURCE_SHA || ''
const rcConfigured = Boolean(adminUsername && adminPassword && /^[0-9a-f]{40}$/.test(sourceSha))

test.describe.configure({ mode: 'serial' })
test.skip(!rcConfigured, 'RC live browser environment is not configured')

test('RC public WebChat message is visible in the authenticated operator surface', async ({ page }) => {
  const message = `RC browser synthetic message ${sourceSha.slice(0, 12)}`

  await page.goto('/webchat/demo/')
  await expect(page.locator('.nd-webchat-panel[data-open="true"]')).toBeVisible({ timeout: 20_000 })
  const input = page.locator('.nd-webchat-input')
  await expect(input).toBeEnabled()
  await input.fill(message)

  const messageRequest = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return response.request().method() === 'POST'
      && /\/api\/webchat\/conversations\/wc_[^/]+\/messages$/.test(url.pathname)
  })
  await page.locator('.nd-webchat-send').click()
  const response = await messageRequest
  expect(response.ok()).toBeTruthy()
  await expect(page.locator('.nd-webchat-msg.visitor', { hasText: message })).toBeVisible()

  await page.goto('/login')
  await page.getByLabel('账号').fill(adminUsername)
  await page.getByLabel('密码').fill(adminPassword)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).not.toHaveURL(/\/login$/)

  await page.goto('/webchat')
  await expect(page.getByTestId('nexus-support-console')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(message, { exact: false }).first()).toBeVisible({ timeout: 20_000 })
})
