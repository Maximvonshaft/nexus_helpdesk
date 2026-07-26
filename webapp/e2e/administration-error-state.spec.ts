import { expect, test } from '@playwright/test'
import { json, mockResponsiveConsole } from './fixtures/responsiveConsole'

test('user-list failure is exclusive with empty and global administration states', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockResponsiveConsole(page)
  await page.route('**/api/admin/identity/roles', (route) => json(route, []))
  await page.route('**/api/admin/identity/teams', (route) => json(route, []))
  await page.route('**/api/lookups/markets', (route) => json(route, []))
  await page.route('**/api/admin/users?*', (route) => json(route, { detail: 'user directory unavailable' }, 503))

  await page.goto('/administration')

  await expect(page.getByRole('heading', { level: 1, name: '系统管理' })).toBeVisible()
  await expect(page.getByText('无法读取用户')).toBeVisible()
  await expect(page.getByText('没有匹配的用户')).toHaveCount(0)
  await expect(page.getByText('无法读取系统管理数据')).toHaveCount(0)
})
