import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

async function seedToken(page: Page) {
  await page.addInitScript(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, 'identity-browser-token'])
}

async function mockForcedRotation(page: Page) {
  await seedToken(page)
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, {
        id: 7,
        username: 'issued-agent',
        display_name: 'Issued Agent',
        email: 'issued-agent@example.test',
        role: 'agent',
        team_id: null,
        capabilities: ['ticket.read', 'operator_queue.read'],
        must_change_password: true,
        password_changed_at: null,
        last_login_at: '2026-07-20T14:00:00Z',
      })
    }
    if (url.pathname === '/api/auth/change-password' && route.request().method() === 'POST') {
      return json(route, { ok: true, reauthenticate: true })
    }
    return json(route, { detail: `Unhandled forced-rotation API ${route.request().method()} ${url.pathname}` }, 404)
  })
}

async function mockAdministration(page: Page, commands: string[]) {
  await seedToken(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/auth/me') {
      return json(route, {
        id: 1,
        username: 'admin',
        display_name: 'Admin User',
        email: 'admin@example.test',
        role: 'admin',
        team_id: null,
        capabilities: ['user.manage', 'security.read', 'audit.read'],
        must_change_password: false,
        password_changed_at: '2026-07-19T08:00:00Z',
        last_login_at: '2026-07-20T14:00:00Z',
      })
    }
    if (path === '/api/admin/identity/roles') {
      return json(route, [
        { role: 'admin', default_capabilities: ['user.manage', 'security.read', 'audit.read'] },
        { role: 'agent', default_capabilities: ['ticket.read', 'operator_queue.read'] },
      ])
    }
    if (path === '/api/admin/identity/teams') return json(route, [])
    if (path === '/api/lookups/markets') return json(route, [])
    if (path === '/api/admin/users' && request.method() === 'GET') {
      return json(route, {
        items: [{
          id: 2,
          username: 'agent-one',
          display_name: 'Agent One',
          email: 'agent-one@example.test',
          role: 'agent',
          team_id: null,
          is_active: true,
          capabilities: ['ticket.read', 'operator_queue.read'],
          created_at: '2026-07-20T10:00:00Z',
          updated_at: '2026-07-20T10:00:00Z',
        }],
        next_cursor: null,
        has_more: false,
        filters: { limit: 100, include_inactive: true },
      })
    }
    if (path === '/api/admin/identity/credential-policies') {
      return json(route, [
        {
          user_id: 1,
          username: 'admin',
          display_name: 'Admin User',
          role: 'admin',
          is_active: true,
          must_change_password: false,
          password_changed_at: '2026-07-19T08:00:00Z',
          last_login_at: '2026-07-20T14:00:00Z',
          updated_at: '2026-07-20T14:00:00Z',
        },
        {
          user_id: 2,
          username: 'agent-one',
          display_name: 'Agent One',
          role: 'agent',
          is_active: true,
          must_change_password: false,
          password_changed_at: null,
          last_login_at: null,
          updated_at: '2026-07-20T10:00:00Z',
        },
      ])
    }
    if (path === '/api/admin/security-audit') {
      return json(route, {
        capability_catalog: [],
        users: [],
        recent_audit: [],
        summary: {
          total_users: 2,
          active_users: 2,
          inactive_users: 0,
          admin_users: 1,
          auditor_users: 0,
          high_risk_overrides: 0,
          recent_audit_24h: 0,
          catalog_size: 0,
          read_only: false,
        },
      })
    }
    if (path === '/api/admin/identity/users/2/require-password-change' && request.method() === 'POST') {
      commands.push('require-password-change')
      return json(route, { ok: true, user_id: 2 })
    }
    if (path === '/api/admin/identity/users/2/revoke-sessions' && request.method() === 'POST') {
      commands.push('revoke-sessions')
      return json(route, { ok: true, user_id: 2 })
    }
    return json(route, { detail: `Unhandled administration API ${request.method()} ${path}` }, 404)
  })
}

test('forced password rotation cannot render the workspace and completes through account recovery', async ({ page }) => {
  await mockForcedRotation(page)
  await page.goto('/workspace')

  await expect(page).toHaveURL(/\/account$/)
  await expect(page.getByRole('heading', { level: 1, name: '账户设置' })).toBeVisible()
  await expect(page.getByText('完成密码修改前，业务页面和实时工作连接均不可使用。')).toBeVisible()
  await expect(page.getByTestId('operator-workspace')).toHaveCount(0)

  const passwordRegion = page.getByRole('region', { name: '修改密码' })
  await passwordRegion.getByRole('textbox', { name: '当前密码' }).fill('Nexus!Issued2026')
  await passwordRegion.getByRole('textbox', { name: '新密码', exact: true }).fill('Nexus!Rotated2026')
  await passwordRegion.getByRole('textbox', { name: '确认新密码' }).fill('Nexus!Rotated2026')
  await passwordRegion.getByRole('button', { name: '更新密码并重新登录' }).click()

  await expect(page).toHaveURL(/\/login$/)
})

test('administrator forces rotation and revokes sessions from the one credential panel', async ({ page }) => {
  const commands: string[] = []
  await mockAdministration(page, commands)
  await page.goto('/administration')

  await page.getByRole('tab', { name: '登录与会话' }).click()
  await expect(page.getByRole('heading', { level: 2, name: '凭据与会话' })).toBeVisible()
  const row = page.getByRole('row', { name: /Agent One/ })

  await row.getByRole('button', { name: '强制改密' }).click()
  const forceDialog = page.getByRole('dialog', { name: '要求修改密码' })
  await forceDialog.getByRole('button', { name: '确认执行' }).click()
  await expect.poll(() => commands).toContain('require-password-change')

  await row.getByRole('button', { name: '撤销会话' }).click()
  const revokeDialog = page.getByRole('dialog', { name: '撤销全部会话' })
  await revokeDialog.getByRole('button', { name: '确认执行' }).click()
  await expect.poll(() => commands).toContain('revoke-sessions')
})
