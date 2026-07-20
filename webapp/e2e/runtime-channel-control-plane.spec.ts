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
  await page.addInitScript(([key, token]) => window.sessionStorage.setItem(key, token), [TOKEN_KEY, 'control-plane-token'])
}

function adminUser(capabilities: string[]) {
  return {
    id: 1,
    username: 'control-admin',
    display_name: 'Control Admin',
    email: 'control-admin@example.test',
    role: 'admin',
    team_id: null,
    capabilities,
    must_change_password: false,
    password_changed_at: '2026-07-20T10:00:00Z',
    last_login_at: '2026-07-20T12:00:00Z',
    mfa_enabled: true,
  }
}

test('runtime manager sees dead queues and requeues through canonical commands', async ({ page }) => {
  const commands: string[] = []
  await seedToken(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/auth/me') return json(route, adminUser(['runtime.manage', 'audit.read']))
    if (path === '/api/admin/provider-runtime/status') {
      return json(route, {
        ok: true,
        status: 'ready',
        app_env: 'test',
        webchat_runtime_enabled: false,
        configured_provider: null,
        fallback_provider: null,
        warnings: [],
        providers: [],
        boundary: {},
      })
    }
    if (path === '/api/support/conversations/metrics') {
      return json(route, { total: 0, needs_human: 0, ai_active: 0, by_channel: {} })
    }
    if (path === '/api/admin/queues/summary') {
      return json(route, {
        pending_jobs: 2,
        dead_jobs: 3,
        external_pending_outbound: 1,
        external_dead_outbound: 4,
      })
    }
    if (path === '/api/admin/jobs/requeue-dead' && request.method() === 'POST') {
      commands.push('jobs')
      return json(route, { ok: true, requeued: 3, job_type: null })
    }
    if (path === '/api/admin/outbound/requeue-dead' && request.method() === 'POST') {
      commands.push('outbound')
      return json(route, { ok: true, requeued: 4 })
    }
    return json(route, { detail: `Unhandled runtime API ${request.method()} ${path}` }, 404)
  })

  await page.goto('/runtime')
  await expect(page.getByRole('heading', { level: 2, name: '队列恢复' })).toBeVisible()
  await expect(page.getByText('失败后台任务')).toBeVisible()

  await page.getByRole('button', { name: '恢复失败后台任务' }).click()
  await page.getByRole('dialog', { name: '恢复失败后台任务' }).getByRole('button', { name: '确认重新排队' }).click()
  await expect.poll(() => commands).toContain('jobs')

  await page.getByRole('button', { name: '恢复失败外部消息' }).click()
  await page.getByRole('dialog', { name: '恢复失败外部消息' }).getByRole('button', { name: '确认重新排队' }).click()
  await expect.poll(() => commands).toContain('outbound')
})

test('channel administrator tests and disables an existing email account from the single channels page', async ({ page }) => {
  const commands: string[] = []
  await seedToken(page)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/auth/me') return json(route, adminUser(['channel_account.manage']))
    if (path === '/api/admin/channel-accounts') return json(route, [])
    if (path === '/api/channel-control/onboarding-tasks') return json(route, { tasks: [], total: 0, limit: 50, offset: 0 })
    if (path === '/api/lookups/markets') return json(route, [{ id: 1, code: 'ME', name: 'Montenegro', country_code: 'ME' }])
    if (path === '/api/admin/outbound-email/accounts') {
      return json(route, [{
        id: 12,
        display_name: 'Operations Mail',
        host: 'smtp.example.test',
        port: 587,
        username: 'operations@example.test',
        from_address: 'operations@example.test',
        reply_to: null,
        security_mode: 'starttls',
        inbound_enabled: true,
        imap_host: 'imap.example.test',
        imap_port: 993,
        imap_username: 'operations@example.test',
        imap_security_mode: 'ssl',
        imap_mailbox: 'INBOX',
        imap_sync_cursor: null,
        imap_last_seen_at: null,
        imap_last_status: 'ready',
        imap_last_error: null,
        imap_last_sync_job_id: null,
        imap_password_configured: true,
        imap_password_mask: '********',
        market_id: 1,
        is_active: true,
        priority: 100,
        health_status: 'healthy',
        last_test_status: 'success',
        last_test_error: null,
        last_test_at: '2026-07-20T12:00:00Z',
        password_configured: true,
        password_mask: '********',
        created_at: '2026-07-20T10:00:00Z',
        updated_at: '2026-07-20T12:00:00Z',
      }])
    }
    if (path === '/api/admin/outbound-email/accounts/12/test-send' && request.method() === 'POST') {
      commands.push('test-send')
      expect(JSON.parse(request.postData() || '{}').to_address).toBe('qa@example.test')
      return json(route, {
        ok: true,
        account_id: 12,
        provider_status: 'accepted',
        failure_code: null,
        error_message: null,
        sent_at: '2026-07-20T13:00:00Z',
        health_status: 'healthy',
      })
    }
    if (path === '/api/admin/outbound-email/accounts/12/disable' && request.method() === 'POST') {
      commands.push('disable')
      return json(route, { id: 12, is_active: false })
    }
    return json(route, { detail: `Unhandled channel API ${request.method()} ${path}` }, 404)
  })

  await page.goto('/channels')
  await expect(page.getByRole('heading', { level: 2, name: '邮件账号' })).toBeVisible()
  const row = page.getByRole('row', { name: /Operations Mail/ })

  await row.getByRole('button', { name: '测试发送' }).click()
  const testDialog = page.getByRole('dialog', { name: '测试邮件发送' })
  await testDialog.getByLabel('测试收件地址').fill('qa@example.test')
  await testDialog.getByRole('button', { name: '发送测试邮件' }).click()
  await expect.poll(() => commands).toContain('test-send')

  await row.getByRole('button', { name: '停用' }).click()
  await page.getByRole('dialog', { name: '停用邮件账号' }).getByRole('button', { name: '确认停用' }).click()
  await expect.poll(() => commands).toContain('disable')
})
