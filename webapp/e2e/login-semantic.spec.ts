import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

const authUser = {
  id: 1,
  username: 'operator',
  display_name: 'Operations User',
  role: 'agent',
  capabilities: ['ticket.read', 'operator_queue.read'],
}

async function fulfillAuth(route: Route, options?: { rejectLogin?: boolean }) {
  const url = new URL(route.request().url())
  if (url.pathname === '/api/auth/login') {
    if (options?.rejectLogin) {
      return route.fulfill({
        status: 401,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ detail: 'raw-private-backend-error' }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify({ access_token: 'operator-token', user: authUser }),
    })
  }
  if (url.pathname === '/api/auth/me') {
    return route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(authUser),
    })
  }
  return route.fulfill({
    status: 404,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({ detail: `Unhandled test API ${url.pathname}` }),
  })
}

async function mockAuth(page: Page, options?: { rejectLogin?: boolean }) {
  await page.route('**/api/**', (route) => fulfillAuth(route, options))
}

test('Login exposes a direct operator purpose, semantic form, visible labels, and password reveal', async ({ page }) => {
  await mockAuth(page)
  await page.goto('/login')

  await expect(page.getByRole('main')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: '登录客服与运营工作台' })).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: '客服与运营工作台' })).toBeVisible()
  await expect(page.getByText('可见国家、渠道和操作权限由当前账号决定。')).toBeVisible()
  await expect(page.getByText('从可信事实到可验证结案')).toHaveCount(0)
  await expect(page.locator('form')).toHaveCount(1)
  await expect(page.getByLabel('账号 必填')).toBeVisible()

  const password = page.getByLabel('密码 必填')
  const reveal = page.getByRole('button', { name: '显示密码' })
  await expect(password).toHaveAttribute('type', 'password')
  await expect(reveal).toHaveAttribute('aria-pressed', 'false')
  await reveal.click()
  await expect(password).toHaveAttribute('type', 'text')
  await expect(page.getByRole('button', { name: '隐藏密码' })).toHaveAttribute('aria-pressed', 'true')
})

test('Enter submits through one root destination and reaches the protected workspace', async ({ page }) => {
  await mockAuth(page)
  await page.goto('/login')

  await page.getByLabel('账号 必填').fill('operator')
  await page.getByLabel('密码 必填').fill('correct-password')
  await page.getByLabel('密码 必填').press('Enter')

  await expect(page).toHaveURL(/\/workspace(?:\?.*)?$/)
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), TOKEN_KEY)).toBe('operator-token')
})

test('Login failure is bounded, announced, and focused without exposing backend detail', async ({ page }) => {
  await mockAuth(page, { rejectLogin: true })
  await page.goto('/login')

  await page.getByLabel('账号 必填').fill('operator')
  await page.getByLabel('密码 必填').fill('wrong-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()

  const alert = page.getByRole('alert')
  await expect(alert).toHaveText('无法登录。请检查账号和密码后重试。')
  await expect(alert).toBeFocused()
  await expect(page.getByText('raw-private-backend-error')).toHaveCount(0)
})

test('375px Login has no horizontal overflow and primary controls meet target size', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockAuth(page)
  await page.goto('/login')

  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  const username = await page.getByLabel('账号 必填').boundingBox()
  const password = await page.getByLabel('密码 必填').boundingBox()
  const reveal = await page.getByRole('button', { name: '显示密码' }).boundingBox()
  const submit = await page.getByRole('button', { name: '登录', exact: true }).boundingBox()

  expect(username?.height ?? 0).toBeGreaterThanOrEqual(44)
  expect(password?.height ?? 0).toBeGreaterThanOrEqual(44)
  expect(reveal?.height ?? 0).toBeGreaterThanOrEqual(44)
  expect(submit?.height ?? 0).toBeGreaterThanOrEqual(44)
})

test('an existing authenticated session leaves Login through the same root authority', async ({ page }) => {
  await page.addInitScript(([key, token]) => sessionStorage.setItem(key, token), [TOKEN_KEY, 'existing-token'])
  await mockAuth(page)
  await page.goto('/login')

  await expect(page).toHaveURL(/\/workspace(?:\?.*)?$/)
})
