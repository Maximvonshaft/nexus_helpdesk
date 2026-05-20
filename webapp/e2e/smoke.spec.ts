import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

function authUser(kind: 'agent' | 'admin') {
  if (kind === 'admin') {
    return {
      id: 1,
      username: 'admin',
      display_name: 'Admin User',
      role: 'admin',
      capabilities: ['runtime.manage', 'channel_account.manage', 'ai_config.read', 'ai_config.manage', 'user.manage'],
    }
  }
  return {
    id: 2,
    username: 'agent',
    display_name: 'Agent User',
    role: 'agent',
    capabilities: [],
  }
}

async function fulfillApi(route: Route, kind: 'agent' | 'admin') {
  const url = new URL(route.request().url())
  const path = url.pathname

  const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body) })

  if (path === '/api/auth/me') return json(authUser(kind))
  if (path === '/api/lookups/bulletins') return json([])
  if (path === '/api/lite/cases') return json({ items: [], next_cursor: null, has_more: false })
  if (path === '/api/admin/queues/summary') return json({ pending_outbound: 0, dead_outbound: 0, pending_jobs: 0, dead_jobs: 0, openclaw_links: 0 })
  if (path === '/api/admin/openclaw/runtime-health') {
    return json({
      stale_link_count: 0,
      pending_sync_jobs: 0,
      dead_sync_jobs: 0,
      pending_attachment_jobs: 0,
      dead_attachment_jobs: 0,
      warnings: [],
    })
  }
  if (path === '/api/admin/production-readiness') {
    return json({
      app_env: 'development',
      database_url_scheme: 'sqlite',
      is_postgres: false,
      storage_backend: 'local',
      openclaw_transport: 'mcp',
      metrics_enabled: false,
      openclaw_sync_enabled: true,
      warnings: [],
    })
  }
  if (path === '/api/admin/signoff-checklist') return json({ status: 'not_ready', checks: {}, warnings: [] })
  if (path === '/api/admin/channel-accounts') return json([])

  return route.fulfill({ status: 404, contentType: 'application/json; charset=utf-8', body: JSON.stringify({ detail: `Unhandled mock for ${path}` }) })
}

async function mockAuthenticatedConsole(page: Page, kind: 'agent' | 'admin') {
  await page.addInitScript(([storageKey, token]) => {
    window.sessionStorage.setItem(storageKey, token)
  }, [TOKEN_KEY, `${kind}-token`])
  await page.route('**/api/**', (route) => fulfillApi(route, kind))
}

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
  await expect(page.getByRole('link', { name: /处理工单/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /控制面/ })).toHaveCount(0)
  await expect(page.getByRole('link', { name: /账号权限/ })).toHaveCount(0)
  await expect(page.getByRole('link', { name: /发送线路/ })).toHaveCount(0)
})

test('admin-capable navigation shows management entry points', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'admin')
  await page.goto('/')

  await expect(page.getByRole('link', { name: /控制面/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /账号权限/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /发送线路/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /运行恢复/ })).toBeVisible()
})
