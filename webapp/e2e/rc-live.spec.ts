import { writeFileSync } from 'node:fs'
import { expect, test, type Page, type Response } from '@playwright/test'

const adminUsername = process.env.RC_TEST_ADMIN_USERNAME || ''
const adminPassword = process.env.RC_TEST_ADMIN_PASSWORD || ''
const sourceSha = process.env.RC_SOURCE_SHA || ''
const baseURL = (process.env.PLAYWRIGHT_BASE_URL || '').replace(/\/+$/, '')
const browserStageFile = process.env.RC_BROWSER_STAGE_FILE || ''
const rcConfigured = Boolean(
  adminUsername
  && adminPassword
  && /^[0-9a-f]{40}$/.test(sourceSha)
  && /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(baseURL),
)

function rcUrl(path: string): string {
  return new URL(path, `${baseURL}/`).toString()
}

function markStage(stage: string): void {
  if (!/^[a-z0-9_-]{1,56}$/.test(stage)) throw new Error('invalid RC browser stage')
  console.log(`RC_BROWSER_STAGE=${stage}`)
  if (browserStageFile) writeFileSync(browserStageFile, `${stage}\n`, { encoding: 'utf8', mode: 0o600 })
}

function classifyBrowserError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  const networkCode = raw.match(/net::(ERR_[A-Z0-9_]+)/)?.[1]
  if (networkCode) return networkCode.toLowerCase().replaceAll('_', '-').slice(0, 48)
  const normalized = raw.toLowerCase()
  if (normalized.includes('timeout')) return 'navigation-timeout'
  if (normalized.includes('target page') || normalized.includes('browser has been closed') || normalized.includes('context has been closed')) return 'target-closed'
  if (normalized.includes('navigation') && normalized.includes('interrupted')) return 'navigation-interrupted'
  return 'unknown-navigation-error'
}

function reportBoundedBrowserError(error: unknown): void {
  const raw = error instanceof Error ? error.message : String(error)
  const bounded = raw
    .replaceAll(baseURL, '{base_url}')
    .replaceAll(adminUsername, '{admin_username}')
    .replaceAll(adminPassword, '{admin_password}')
    .replaceAll(sourceSha, '{source_sha}')
    .slice(0, 400)
  const detailHex = Buffer.from(bounded, 'utf8').toString('hex')
  console.log(`RC_BROWSER_DETAIL_HEX=${detailHex}`)
  markStage(`error-${classifyBrowserError(error)}`)
}

async function navigate(page: Page, path: string): Promise<Response | null> {
  try {
    return await page.goto(rcUrl(path), { waitUntil: 'commit', timeout: 20_000 })
  } catch (error) {
    reportBoundedBrowserError(error)
    throw error
  }
}

test.describe.configure({ mode: 'serial' })
test.skip(!rcConfigured, 'RC live browser environment is not configured')

test('RC public WebChat message is persisted and the authenticated customer-service workspace is available', async ({ page }) => {
  const message = `RC browser synthetic message ${sourceSha.slice(0, 12)}`

  markStage('public-navigation')
  const navigationResponse = await navigate(page, '/webchat/demo/')
  markStage('public-committed')
  expect(navigationResponse).not.toBeNull()
  expect(navigationResponse?.ok()).toBeTruthy()

  markStage('public-page')
  await expect(page.locator('script[data-auto-open="true"]')).toHaveCount(1, { timeout: 20_000 })

  markStage('public-widget')
  await expect.poll(
    () => page.evaluate(() => typeof (window as typeof window & { NexusDeskWebChat?: unknown }).NexusDeskWebChat === 'object'),
    { timeout: 20_000 },
  ).toBe(true)
  await expect(page.locator('.nd-webchat-panel[data-open="true"]')).toBeVisible({ timeout: 20_000 })
  const input = page.locator('.nd-webchat-input')
  await expect(input).toBeEnabled({ timeout: 20_000 })

  markStage('public-send')
  await input.fill(message)
  const messageRequest = page.waitForResponse((candidate) => {
    const url = new URL(candidate.url())
    return candidate.request().method() === 'POST' && /\/api\/webchat\/conversations\/wc_[^/]+\/messages$/.test(url.pathname)
  })
  await page.locator('.nd-webchat-send').click()
  const messageResponse = await messageRequest
  expect(messageResponse.ok()).toBeTruthy()
  await expect(page.locator('.nd-webchat-msg.visitor', { hasText: message })).toBeVisible()

  markStage('login-navigation')
  const loginResponse = await navigate(page, '/login')
  expect(loginResponse).not.toBeNull()
  expect(loginResponse?.ok()).toBeTruthy()
  await page.getByLabel(/账号/).fill(adminUsername)
  await page.getByLabel(/密码/).fill(adminPassword)
  await page.getByRole('button', { name: '登录客服工作台' }).click()
  await expect(page).not.toHaveURL(/\/login$/)

  markStage('operator-navigation')
  const operatorResponse = await navigate(page, '/workspace')
  expect(operatorResponse).not.toBeNull()
  expect(operatorResponse?.ok()).toBeTruthy()
  await expect(page.getByRole('heading', { level: 1, name: '客服工作台' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  await expect(page.locator('body')).not.toContainText(/\b(?:AI|Artificial Intelligence|Runtime|Provider|RAG|Prompt|Model|Agent)\b/i)

  markStage('completed')
})
