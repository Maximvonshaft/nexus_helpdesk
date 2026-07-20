import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

function adminUser(mustChangePassword = false) {
  return {
    id: 1,
    username: 'admin',
    display_name: 'Admin User',
    email: 'admin@example.test',
    role: 'admin',
    team_id: 1,
    capabilities: ['user.manage', 'security.read', 'audit.read', 'runtime.manage'],
    must_change_password: mustChangePassword,
    password_changed_at: null,
    last_login_at: '2026-07-20T12:00:00Z',
  }
}

function managedUser() {
  return {
    id: 2,
    username: 'agent-one',
    display_name: 'Agent One',
    email: 'agent@example.test',
    role: 'agent',
    team_id: 1,
    is_active: true,
    capabilities: ['ticket.read', 'operator_queue.read'],
    created_at: '2026-07-20T10:00:00Z',
    updated_at: '2026-07-20T10:00:00Z',
  }
}

async function installIdentityMocks(page: Page, options?: { mustChangePassword?: boolean }) {
  await page.addInitScript(([key, token]) => sessionStorage.setItem(key, token), [TOKEN_KEY, 'identity-token'])
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/auth/me') return json(route, adminUser(Boolean(options?.mustChangePassword)))
    if (path === '/api/auth/security') {
      return json(route, {
        user_id: 1,
        session_version: 1,
        must_change_password: Boolean(options?.mustChangePassword),
        password_changed_at: null,
        last_login_at: '2026-07-20T12:00:00Z',
        updated_at: '2026-07-20T12:00:00Z',
      })
    }
    if (path === '/api/auth/change-password' && request.method() === 'POST') {
      return json(route, {
        access_token: 'rotated-token',
        token_type: 'bearer',
        user: adminUser(false),
      })
    }
    if (path === '/api/admin/users' && request.method() === 'GET') {
      return json(route, {
        items: [managedUser()],
        next_cursor: null,
        has_more: false,
        filters: { limit: 100, include_inactive: true },
      })
    }
    if (path === '/api/admin/users' && request.method() === 'POST') {
      const body = JSON.parse(request.postData() || '{}')
      return json(route, {
        id: 3,
        ...body,
        is_active: true,
        capabilities: body.capabilities || [],
        created_at: '2026-07-20T13:00:00Z',
        updated_at: '2026-07-20T13:00:00Z',
      })
    }
    if (path === '/api/lookups/teams') return json(route, [{ id: 1, name: 'Customer Care', team_type: 'support', market_id: 1 }])
    if (path === '/api/admin/roles') {
      return json(route, [
        { role: 'admin', capabilities: ['user.manage', 'security.read', 'audit.read', 'runtime.manage'] },
        { role: 'manager', capabilities: ['ticket.read', 'ticket.assign'] },
        { role: 'lead', capabilities: ['ticket.read', 'ticket.assign'] },
        { role: 'agent', capabilities: ['ticket.read', 'operator_queue.read'] },
        { role: 'auditor', capabilities: ['ticket.read', 'security.read', 'audit.read'] },
      ])
    }
    if (path === '/api/admin/capabilities/catalog') {
      return json(route, ['audit.read', 'operator_queue.read', 'security.read', 'ticket.read', 'user.manage'])
    }
    if (path === '/api/admin/user-security-states') {
      return json(route, [{ user_id: 2, session_version: 1, must_change_password: true, last_login_at: null }])
    }
    if (path === '/api/admin/security-audit') {
      return json(route, {
        capability_catalog: ['audit.read', 'operator_queue.read', 'security.read', 'ticket.read', 'user.manage'],
        users: [{
          user_id: 2,
          username: 'agent-one',
          display_name: 'Agent One',
          role: 'agent',
          is_active: true,
          effective_capabilities: ['ticket.read', 'operator_queue.read'],
          override_count: 0,
          high_risk_count: 0,
        }],
        recent_audit: [{
          id: 10,
          actor_id: 1,
          actor_username: 'admin',
          actor_display_name: 'Admin User',
          action: 'user.create',
          target_type: 'user',
          target_id: 2,
          old_value: null,
          new_value: {},
          created_at: '2026-07-20T10:00:00Z',
        }],
        summary: {
          total_users: 2,
          active_users: 2,
          inactive_users: 0,
          admin_users: 1,
          auditor_users: 0,
          high_risk_overrides: 0,
          recent_audit_24h: 1,
          catalog_size: 5,
          read_only: false,
        },
      })
    }
    if (path === '/api/admin/provider-runtime/status') {
      return json(route, { ok: true, status: 'ready', app_env: 'test', webchat_runtime_enabled: false, configured_provider: null, fallback_provider: null, warnings: [], providers: [], boundary: {} })
    }
    if (path === '/api/support/conversations/metrics') return json(route, { total: 0, needs_human: 0, ai_active: 0, by_channel: {} })

    return json(route, { detail: `Unhandled identity API ${request.method()} ${path}` }, 404)
  })
}

test('administrator creates an account from the one canonical control plane', async ({ page }) => {
  await installIdentityMocks(page)
  await page.goto('/administration')

  await expect(page.getByRole('heading', { level: 1, name: '管理控制台' })).toBeVisible()
  await expect(page.getByText('Agent One')).toBeVisible()
  await page.getByRole('button', { name: '创建账号' }).click()

  const dialog = page.getByRole('dialog', { name: '创建账号' })
  await dialog.getByRole('textbox', { name: '登录账号' }).fill('agent-two')
  await dialog.getByRole('textbox', { name: '显示名称' }).fill('Agent Two')
  await dialog.getByRole('textbox', { name: '邮箱' }).fill('agent-two@example.test')
  await dialog.getByLabel('初始密码').fill('Initial-Password-789!')
  await dialog.getByRole('button', { name: '创建账号' }).click()

  await expect(dialog).not.toBeVisible()
})

test('operator rotates a password and receives durable success', async ({ page }) => {
  await installIdentityMocks(page)
  await page.goto('/account')

  await expect(page.getByRole('heading', { level: 1, name: '账号与安全' })).toBeVisible()
  await page.getByLabel('当前密码').fill('Current-Password-123!')
  await page.getByLabel('新密码', { exact: true }).fill('Replacement-Password-456!')
  await page.getByLabel('确认新密码').fill('Replacement-Password-456!')
  await page.getByRole('button', { name: '更新密码' }).click()

  await expect(page.getByText('密码已修改，旧会话已撤销。')).toBeVisible()
})

test('forced password rotation prevents entry into another protected domain', async ({ page }) => {
  await installIdentityMocks(page, { mustChangePassword: true })
  await page.goto('/runtime')

  await expect(page).toHaveURL(/\/account$/)
  await expect(page.getByRole('heading', { level: 1, name: '账号与安全' })).toBeVisible()
  await expect(page.getByText('该账号使用的是管理员签发或重置的密码。')).toBeVisible()
})
