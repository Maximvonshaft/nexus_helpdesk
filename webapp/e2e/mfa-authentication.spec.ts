import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

function authUser(overrides: Record<string, unknown> = {}) {
  return {
    id: 9,
    username: 'mfa-agent',
    display_name: 'MFA Agent',
    email: 'mfa-agent@example.test',
    role: 'agent',
    team_id: null,
    capabilities: ['ticket.read', 'operator_queue.read'],
    must_change_password: false,
    password_changed_at: '2026-07-20T10:00:00Z',
    last_login_at: '2026-07-20T12:00:00Z',
    mfa_enabled: true,
    ...overrides,
  }
}

async function seedToken(page: Page) {
  await page.addInitScript(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, 'mfa-account-token'])
}

test('password login does not store an access token until MFA challenge verification succeeds', async ({ page }) => {
  let workspaceScopeRequests = 0
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/auth/login' && request.method() === 'POST') {
      return json(route, {
        mfa_required: true,
        challenge_token: 'five-minute-mfa-challenge',
        expires_in_seconds: 300,
        display_name: 'MFA Agent',
      })
    }
    if (path === '/api/auth/mfa/login/verify' && request.method() === 'POST') {
      expect(JSON.parse(request.postData() || '{}')).toEqual({
        challenge_token: 'five-minute-mfa-challenge',
        credential: '123456',
      })
      return json(route, {
        access_token: 'verified-mfa-token',
        token_type: 'bearer',
        user: authUser(),
      })
    }
    if (path === '/api/auth/me') return json(route, authUser())
    if (path === '/api/admin/operator-queue/my-scopes') {
      workspaceScopeRequests += 1
      return json(route, {
        items: [{ tenant_key: 'tenant-mfa', tenant_hash: '123456789abc', country_code: 'ME', channel_key: 'webchat' }],
        requires_explicit_admin_scope: false,
      })
    }
    if (path === '/api/admin/operator-queue/unified') {
      return json(route, {
        items: [],
        next_cursor: null,
        scope: { tenant_hash: '123456789abc', country_code: 'ME', channel_key: 'webchat' },
        filters: { state: 'active', source_type: null, owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
      })
    }
    return json(route, { detail: `Unhandled MFA login API ${request.method()} ${path}` }, 404)
  })

  await page.goto('/login')
  await page.getByLabel('账号').fill('mfa-agent')
  await page.getByLabel('密码').fill('Nexus!Mfa2026')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page.getByRole('heading', { level: 1, name: '两步验证' })).toBeVisible()
  expect(await page.evaluate((key) => window.sessionStorage.getItem(key), TOKEN_KEY)).toBeNull()
  expect(workspaceScopeRequests).toBe(0)

  await page.getByLabel('验证码或恢复码').fill('123456')
  await page.getByRole('button', { name: '验证并登录' }).click()

  await expect(page).toHaveURL(/\/workspace$/)
  await expect(page.getByTestId('operator-workspace')).toBeVisible()
  expect(await page.evaluate((key) => window.sessionStorage.getItem(key), TOKEN_KEY)).toBe('verified-mfa-token')
  expect(workspaceScopeRequests).toBeGreaterThan(0)
})

test('account MFA setup displays recovery codes once and returns to login after acknowledgement', async ({ page }) => {
  await seedToken(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/auth/me') return json(route, authUser({ mfa_enabled: false }))
    if (path === '/api/auth/mfa/status') {
      return json(route, {
        enabled: false,
        setup_pending: false,
        confirmed_at: null,
        last_verified_at: null,
        recovery_codes_remaining: 0,
      })
    }
    if (path === '/api/auth/mfa/setup/begin' && request.method() === 'POST') {
      expect(JSON.parse(request.postData() || '{}')).toEqual({ current_password: 'Nexus!Mfa2026' })
      return json(route, {
        secret: 'JBSWY3DPEHPK3PXP',
        otpauth_uri: 'otpauth://totp/Nexus%20OSR%3Amfa-agent?secret=JBSWY3DPEHPK3PXP&issuer=Nexus+OSR',
      })
    }
    if (path === '/api/auth/mfa/setup/confirm' && request.method() === 'POST') {
      expect(JSON.parse(request.postData() || '{}')).toEqual({ code: '654321' })
      return json(route, {
        ok: true,
        recovery_codes: ['AAAAA-BBBBB', 'CCCCC-DDDDD', 'EEEEE-FFFFF'],
        reauthenticate: true,
      })
    }
    return json(route, { detail: `Unhandled MFA setup API ${request.method()} ${path}` }, 404)
  })

  await page.goto('/account')
  await expect(page.getByRole('heading', { level: 2, name: '两步验证' })).toBeVisible()
  await page.getByLabel('当前密码').last().fill('Nexus!Mfa2026')
  await page.getByRole('button', { name: '开始启用' }).click()

  await expect(page.getByText('JBSWY3DPEHPK3PXP')).toBeVisible()
  await page.getByLabel('6 位验证码').fill('654321')
  await page.getByRole('button', { name: '确认并启用' }).click()

  const recoveryDialog = page.getByRole('dialog', { name: '保存恢复码' })
  await expect(recoveryDialog.getByText('AAAAA-BBBBB')).toBeVisible()
  await expect(recoveryDialog.getByText('CCCCC-DDDDD')).toBeVisible()
  await recoveryDialog.getByRole('button', { name: '已安全保存，重新登录' }).click()

  await expect(page).toHaveURL(/\/login$/)
  expect(await page.evaluate((key) => window.sessionStorage.getItem(key), TOKEN_KEY)).toBeNull()
})
