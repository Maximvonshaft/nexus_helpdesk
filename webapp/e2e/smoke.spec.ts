import { expect, test } from '@playwright/test'
import { mockAuthenticatedConsole } from './support/mockConsole'

test('login page renders', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '客服工作台' })).toBeVisible()
  await expect(page.getByLabel('账号')).toBeVisible()
  await expect(page.getByRole('button', { name: '登录' })).toBeVisible()
})

test('unauthenticated protected route redirects back to login', async ({ page }) => {
  await page.goto('/users')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByText('登录状态只保存在当前浏览器会话中。')).toBeVisible()
})

test('agent navigation hides management entry points', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'agent')
  await page.goto('/')

  await expect(page.getByTestId('operator-primary-navigation')).toBeVisible()
  await expect(page.getByRole('link', { name: /今日工作台/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /WebChat/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /Customer 360/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /Provider Credentials/ })).toHaveCount(0)
  await expect(page.getByRole('link', { name: /Outbound Email/ })).toHaveCount(0)
  await expect(page.getByRole('link', { name: /^Users$/ })).toHaveCount(0)
  await expect(page.getByRole('link', { name: /WebCall AI Demo/ })).toHaveCount(0)
})

test('admin-capable navigation shows management entry points', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'admin')
  await page.goto('/')

  await expect(page.getByRole('link', { name: /Provider Credentials/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /Outbound Email/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /^Users$/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /Provider \/ Runtime Health/ })).toBeVisible()
})

test('admin can open outbound email configuration page without exposing password', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'admin')
  await page.goto('/outbound-email')

  await expect(page.getByRole('heading', { name: 'SMTP 账号配置' })).toBeVisible()
  await expect(page.getByText('Pilot SMTP')).toBeVisible()
  await expect(page.getByText('测试发送会发出真实邮件')).toBeVisible()
  await expect(page.getByRole('button', { name: '发送测试邮件' })).toBeVisible()
  await expect(page.getByText('密码：********')).toBeVisible()
  await expect(page.getByText(/smtp-password|secret|Bearer/i)).toHaveCount(0)
})
