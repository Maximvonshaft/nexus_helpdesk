import { expect, test } from '@playwright/test'
import {
  canonicalRoutes,
  json,
  mockResponsiveConsole,
} from './fixtures/responsiveConsole'

for (const viewport of [
  { width: 375, height: 812 },
  { width: 768, height: 1024 },
  { width: 1024, height: 900 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 1000 },
]) {
  test(`${viewport.width}px canonical routes stay inside the viewport`, async ({ page }) => {
    test.setTimeout(120_000)
    await page.setViewportSize(viewport)
    await mockResponsiveConsole(page)

    for (const route of canonicalRoutes) {
      await page.goto(route.path)
      await expect(route.ready(page)).toBeVisible()
      await expect(page.getByRole('main')).toBeVisible()
      await expect(page).toHaveTitle(route.title)
      await expect.poll(
        () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        { message: `${route.path} overflowed at ${viewport.width}px` },
      ).toBe(true)
    }
  })
}

test('mobile Drawer exposes live controls while their runtimes remain mounted when closed', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  let voicePolls = 0
  let agentStateReads = 0
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (request.method() === 'GET' && url.pathname === '/api/webchat/admin/voice/sessions') voicePolls += 1
    if (request.method() === 'GET' && url.pathname === '/api/operator/agent-state') agentStateReads += 1
  })
  await mockResponsiveConsole(page)
  await page.goto('/workspace')
  await expect(page.getByTestId('operator-workspace')).toBeVisible()

  const menu = page.getByRole('button', { name: '打开主导航' })
  const logout = page.getByRole('button', { name: '退出', exact: true })
  await expect(menu).toBeVisible()
  await expect(logout).toHaveCount(1)
  expect((await logout.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await expect(page.locator('#nd-mobile-navigation')).toHaveCount(0)
  await expect(page.getByLabel('当前工作范围')).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: '客服状态' })).toHaveCount(0)
  await expect.poll(() => agentStateReads).toBeGreaterThanOrEqual(1)
  await expect.poll(() => voicePolls, { timeout: 5_000 }).toBeGreaterThanOrEqual(2)

  await menu.click()
  const drawer = page.locator('#nd-mobile-navigation')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('navigation', { name: '主导航' })).toBeVisible()
  await expect(drawer.getByLabel('当前工作范围')).toBeVisible()
  await expect(page.getByLabel('当前工作范围')).toHaveCount(1)
  await expect(drawer.getByRole('combobox', { name: '客服状态' })).toBeVisible()
  await expect(drawer.getByRole('switch', { name: '关闭电话接线' })).toBeVisible()
  await expect(drawer.getByText(/显示时区：/)).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(drawer).toHaveCount(0)
  await expect(menu).toBeFocused()

  await menu.click()
  await drawer.getByRole('link', { name: '知识库' }).click()
  await expect(page).toHaveURL(/\/knowledge$/)
  await expect(page.getByRole('heading', { level: 1, name: '知识与流程' })).toBeVisible()
  await expect(page.locator('#nd-mobile-navigation')).toHaveCount(0)
})

test('incoming voice dialog remains active with the mobile Drawer closed', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockResponsiveConsole(page)
  await page.route('**/api/webchat/admin/voice/sessions?*', (route) => json(route, {
    items: [{
      ok: true,
      voice_session_id: 'voice-mobile-1',
      status: 'ringing',
      provider: 'livekit',
      media_plane: 'livekit',
      voice_offer: {
        id: 'offer-mobile-1',
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      },
      ticket_id: null,
      ticket_no: null,
      ticket_title: null,
      conversation_id: 'conversation-mobile-1',
      visitor_label: 'Mobile caller',
      direction: 'inbound',
      mode: 'human_first',
    }],
  }))
  await page.goto('/workspace')

  await expect(page.locator('#nd-mobile-navigation')).toHaveCount(0)
  const dialog = page.getByRole('dialog', { name: '新的语音来电' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('Mobile caller')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '接听通话' })).toBeVisible()
  await expect(dialog.locator('[aria-live="polite"]')).toHaveCount(0)
})

test('desktop shell exposes one visible navigation and one work scope', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockResponsiveConsole(page)
  await page.goto('/workspace')
  await expect(page.getByTestId('operator-workspace')).toBeVisible()

  await expect(page.getByRole('button', { name: '打开主导航' })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  await expect(page.getByLabel('当前工作范围')).toHaveCount(1)
  await expect(page.getByRole('combobox', { name: '客服状态' })).toHaveCount(1)
  await expect(page.locator('#nd-mobile-navigation')).toHaveCount(0)
})

test('200 percent text enlargement preserves navigation and required actions', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockResponsiveConsole(page)
  await page.addStyleTag({ content: 'html { font-size: 200% !important; }' })
  await page.goto('/workspace')

  await expect(page.getByRole('button', { name: '打开主导航' })).toBeVisible()
  await expect(page.getByRole('tab', { name: '待处理' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
