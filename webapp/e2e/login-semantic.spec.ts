import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const authUser = { id: 1, username: 'operator', display_name: '客服一号', role: 'agent', capabilities: ['ticket.read', 'operator_queue.read'] }

async function mockAuth(page: Page, options?: { rejectLogin?: boolean }) {
  await page.route('**/api/**', async (route: Route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/login') {
      return route.fulfill({
        status: options?.rejectLogin ? 401 : 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(options?.rejectLogin ? { detail: 'private-backend-detail' } : { access_token: 'operator-token', user: authUser }),
      })
    }
    if (path === '/api/auth/me') return route.fulfill({ status: 200, contentType: 'application/json; charset=utf-8', body: JSON.stringify(authUser) })
    return route.fulfill({ status: 404, contentType: 'application/json; charset=utf-8', body: JSON.stringify({ detail: path }) })
  })
}

test('login is a customer-service flow with visible labels and password reveal', async ({ page }) => {
  await mockAuth(page)
  await page.goto('/login')
  await expect(page.getByRole('heading', { level: 1, name: '进入客服工作台' })).toBeVisible()
  await expect(page.getByText('把客户问题处理到结果')).toBeVisible()
  const password = page.getByLabel('密码 必填')
  await expect(password).toHaveAttribute('type', 'password')
  await page.getByRole('button', { name: '显示密码' }).click()
  await expect(password).toHaveAttribute('type', 'text')
})

test('enter submits to the single workspace authority', async ({ page }) => {
  await mockAuth(page)
  await page.goto('/login')
  await page.getByLabel('账号 必填').fill('operator')
  await page.getByLabel('密码 必填').fill('correct-password')
  await page.getByLabel('密码 必填').press('Enter')
  await expect(page).toHaveURL(/\/workspace(?:\?.*)?$/)
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), TOKEN_KEY)).toBe('operator-token')
})

test('login failure is bounded and does not expose backend detail', async ({ page }) => {
  await mockAuth(page, { rejectLogin: true })
  await page.goto('/login')
  await page.getByLabel('账号 必填').fill('operator')
  await page.getByLabel('密码 必填').fill('wrong-password')
  await page.getByRole('button', { name: '登录客服工作台' }).click()
  await expect(page.getByRole('alert')).toHaveText('无法登录。请检查账号和密码后重试。')
  await expect(page.getByText('private-backend-detail')).toHaveCount(0)
})

test('375px login has no horizontal overflow and controls meet touch target size', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockAuth(page)
  await page.goto('/login')
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  for (const locator of [page.getByLabel('账号 必填'), page.getByLabel('密码 必填'), page.getByRole('button', { name: '显示密码' }), page.getByRole('button', { name: '登录客服工作台' })]) {
    expect((await locator.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
})
