import { expect, test, type Page, type Response } from '@playwright/test'

const adminUsername = process.env.RC_TEST_ADMIN_USERNAME || ''
const adminPassword = process.env.RC_TEST_ADMIN_PASSWORD || ''
const sourceSha = process.env.RC_SOURCE_SHA || ''
const baseURL = (process.env.PLAYWRIGHT_BASE_URL || '').replace(/\/+$/, '')
const rcConfigured = Boolean(
  adminUsername
  && adminPassword
  && /^[0-9a-f]{40}$/.test(sourceSha)
  && /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(baseURL),
)

function rcUrl(path: string): string {
  return new URL(path, `${baseURL}/`).toString()
}

function reportBoundedBrowserError(error: unknown): void {
  const raw = error instanceof Error ? error.message : String(error)
  const bounded = raw
    .replaceAll(baseURL, '{base_url}')
    .replaceAll(adminUsername, '{admin_username}')
    .replaceAll(adminPassword, '{admin_password}')
    .replaceAll(sourceSha, '{source_sha}')
    .slice(0, 400)
  console.log(`RC_BROWSER_DETAIL_HEX=${Buffer.from(bounded, 'utf8').toString('hex')}`)
}

async function navigate(page: Page, path: string): Promise<Response | null> {
  try {
    return await page.goto(rcUrl(path), {
      waitUntil: 'commit',
      timeout: 20_000,
    })
  } catch (error) {
    reportBoundedBrowserError(error)
    throw error
  }
}

test.describe.configure({ mode: 'serial' })
test.skip(!rcConfigured, 'RC live browser environment is not configured')

test('RC public WebChat message is visible in the authenticated operator surface', async ({ page }) => {
  const message = `RC browser synthetic message ${sourceSha.slice(0, 12)}`

  console.log('RC_BROWSER_STAGE=public-navigation')
  await test.step('public WebChat widget loads and initializes its server session', async () => {
    const widgetRequest = page.waitForResponse((response) => {
      const url = new URL(response.url())
      return url.pathname === '/webchat/widget.js'
    })
    const initRequest = page.waitForResponse((response) => {
      const url = new URL(response.url())
      return response.request().method() === 'POST' && url.pathname === '/api/webchat/init'
    })

    const navigationResponse = await navigate(page, '/webchat/demo/')
    console.log('RC_BROWSER_STAGE=public-committed')
    expect(navigationResponse).not.toBeNull()
    expect(navigationResponse?.ok()).toBeTruthy()

    console.log('RC_BROWSER_STAGE=public-page')
    await expect(page.locator('script[data-auto-open="true"]')).toHaveCount(1, { timeout: 20_000 })

    console.log('RC_BROWSER_STAGE=public-widget')
    const widgetResponse = await widgetRequest
    expect(widgetResponse.ok()).toBeTruthy()
    await expect.poll(
      () => page.evaluate(() => typeof (window as typeof window & { NexusDeskWebChat?: unknown }).NexusDeskWebChat === 'object'),
      { timeout: 20_000 },
    ).toBe(true)
    await expect(page.locator('.nd-webchat-panel[data-open="true"]')).toBeVisible({ timeout: 20_000 })

    console.log('RC_BROWSER_STAGE=public-init')
    const initResponse = await initRequest
    expect(initResponse.ok()).toBeTruthy()
  })

  console.log('RC_BROWSER_STAGE=public-send')
  await test.step('public WebChat persists the browser message', async () => {
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

    console.log('RC_BROWSER_STAGE=public-persisted')
    await expect(page.locator('.nd-webchat-msg.visitor', { hasText: message })).toBeVisible()
  })

  console.log('RC_BROWSER_STAGE=login-navigation')
  await test.step('isolated operator authentication succeeds', async () => {
    const loginResponse = await navigate(page, '/login')
    console.log('RC_BROWSER_STAGE=login-form')
    expect(loginResponse).not.toBeNull()
    expect(loginResponse?.ok()).toBeTruthy()
    await page.getByLabel('账号').fill(adminUsername)
    await page.getByLabel('密码').fill(adminPassword)
    await page.getByRole('button', { name: '登录' }).click()
    await expect(page).not.toHaveURL(/\/login$/)
  })

  console.log('RC_BROWSER_STAGE=operator-navigation')
  await test.step('operator selects the matching conversation and sees the same message', async () => {
    const operatorResponse = await navigate(page, '/webchat')
    console.log('RC_BROWSER_STAGE=operator-console')
    expect(operatorResponse).not.toBeNull()
    expect(operatorResponse?.ok()).toBeTruthy()
    await expect(page.getByTestId('nexus-support-console')).toBeVisible({ timeout: 20_000 })

    // The workbench selects its first queue item by default, which is not a
    // stable identity assertion. Locate the synthetic conversation by its
    // server-returned latest-message preview, explicitly select that row, and
    // only then assert the durable message body in the active thread.
    console.log('RC_BROWSER_STAGE=operator-row')
    const matchingRow = page.locator('button.support-row', { hasText: message }).first()
    await expect(matchingRow).toBeVisible({ timeout: 25_000 })
    await matchingRow.click()
    console.log('RC_BROWSER_STAGE=operator-message')
    await expect(page.locator('.support-message-body', { hasText: message }).first()).toBeVisible({ timeout: 20_000 })
  })

  console.log('RC_BROWSER_STAGE=completed')
})
