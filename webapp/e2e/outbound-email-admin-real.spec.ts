import { expect, test } from '@playwright/test'

const shouldRun = process.env.NEXUS_REAL_ADMIN_SMOKE === '1'

test.describe('real admin outbound email smoke', () => {
  test.skip(!shouldRun, 'Set NEXUS_REAL_ADMIN_SMOKE=1 with real admin credentials to run this staging/production smoke.')

  test('admin can log in and open outbound email configuration', async ({ page }) => {
    const username = process.env.NEXUS_ADMIN_USERNAME
    const password = process.env.NEXUS_ADMIN_PASSWORD
    if (!username || !password) {
      throw new Error('NEXUS_ADMIN_USERNAME and NEXUS_ADMIN_PASSWORD are required for real admin smoke')
    }

    await page.goto('/login')
    await page.getByLabel('账号').fill(username)
    await page.getByLabel('密码').fill(password)
    await page.getByRole('button', { name: '登录' }).click()

    await expect(page.getByTestId('operator-primary-navigation')).toBeVisible()
    await page.goto('/outbound-email')
    await expect(page.getByRole('heading', { name: 'SMTP 账号配置' })).toBeVisible()
    await expect(page.getByText('测试发送会发出真实邮件')).toBeVisible()
    await expect(page.getByLabel('测试收件人')).toBeVisible()
    await expect(page.getByRole('button', { name: '发送测试邮件' })).toBeVisible()
  })
})
