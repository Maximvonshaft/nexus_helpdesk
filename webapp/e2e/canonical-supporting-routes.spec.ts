import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

const authUser = {
  id: 7,
  username: 'platform-operator',
  display_name: 'Platform Operator',
  role: 'admin',
  capabilities: [
    'operator_queue.read',
    'ticket.read',
    'channel_account.manage',
    'runtime.manage',
    'audit.read',
  ],
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

async function mockSupportingRoutes(page: Page) {
  await page.addInitScript(([key, token]) => sessionStorage.setItem(key, token), [TOKEN_KEY, 'operator-token'])
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, authUser)
    if (url.pathname === '/api/admin/channel-accounts') {
      return json(route, [{
        id: 1,
        provider: 'whatsapp',
        account_id: 'wa-primary-private',
        display_name: 'WhatsApp 主线路',
        market_id: 1,
        is_active: true,
        priority: 10,
        health_status: 'connected',
        fallback_account_id: null,
        updated_at: '2026-07-14T12:00:00Z',
      }])
    }
    if (url.pathname === '/api/admin/whatsapp/accounts/wa-primary-private/status') {
      return json(route, {
        account_id: 'wa-primary-private',
        status: 'connected',
        qr_status: 'not_required',
        qr: null,
        qr_data_url: null,
        phone_number: '+41790001234',
        jid: 'private-jid',
        last_qr_generated_at: null,
        last_connected_at: '2026-07-14T12:00:00Z',
        last_disconnected_at: null,
        last_error_code: null,
        last_error_message: null,
        reconnect_count: 1,
        channel_account_id: 1,
        channel_health_status: 'connected',
      })
    }
    if (url.pathname === '/api/admin/provider-runtime/status') {
      return json(route, {
        ok: true,
        status: 'ready',
        app_env: 'production',
        webchat_runtime_enabled: true,
        configured_provider: 'private_ai_runtime',
        fallback_provider: null,
        warnings: [],
        boundary: {
          secret_values_exposed: false,
          external_network_call: false,
          customer_message_sent: false,
        },
        providers: [{
          name: 'private_ai_runtime',
          selected: true,
          feature_enabled: true,
          configured: true,
          runtime: 'private',
          capabilities: { chat: true, rag: true },
          diagnostics: {
            chat_mode: 'direct',
            request_shape: 'responses',
            direct_model: 'internal-model-name',
            rag_model: 'internal-rag-model-name',
          },
        }],
      })
    }
    if (url.pathname === '/api/support/conversations/metrics') {
      return json(route, {
        total: 120,
        needs_human: 8,
        ai_active: 30,
        by_channel: { webchat: 80, whatsapp: 40 },
        runtime_latency: {
          sample_count: 40,
          total_turn: { p50_ms: 800, p90_ms: 1800 },
          runtime_total: { p50_ms: 600, p90_ms: 1400 },
          runtime_eval: { p50_ms: 350, p90_ms: 900 },
          cold_load_count: 2,
          slow_prompt_eval_count: 1,
        },
      })
    }
    return json(route, { detail: `Unhandled test API ${url.pathname}` }, 404)
  })
}

test('Channels is a canonical route in the shared shell and keeps identifiers secondary', async ({ page }) => {
  await mockSupportingRoutes(page)
  await page.goto('/channels')

  await expect(page).toHaveURL(/\/channels$/)
  await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '渠道管理', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { level: 1, name: '渠道管理' })).toBeVisible()
  await expect(page.getByText('WhatsApp 主线路')).toBeVisible()
  await expect(page.getByText('•••• 1234')).toBeVisible()
  await expect(page.getByText('+41790001234')).toHaveCount(0)
  await expect(page.getByText('wa-primary-private')).toBeHidden()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('Runtime primary hierarchy stays operational while model diagnostics remain collapsed', async ({ page }) => {
  await mockSupportingRoutes(page)
  await page.goto('/runtime')

  await expect(page).toHaveURL(/\/runtime$/)
  await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '系统运行', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { level: 1, name: '系统运行' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '系统状态' })).toBeVisible()
  await expect(page.getByText('处理方式').locator('..')).toContainText('自动处理')
  await expect(page.getByText('internal-model-name')).toBeHidden()
  await expect(page.getByText('internal-rag-model-name')).toBeHidden()
  await expect(page.getByText('会话总量').locator('..')).toContainText('120')

  await page.getByRole('button', { name: /系统信息/ }).click()
  await expect(page.getByText('internal-model-name')).toBeVisible()
})

test('supporting routes fail closed for an account without the required capability', async ({ page }) => {
  await page.addInitScript(([key, token]) => sessionStorage.setItem(key, token), [TOKEN_KEY, 'operator-token'])
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, { ...authUser, capabilities: ['operator_queue.read', 'ticket.read'] })
    }
    return json(route, { detail: 'should not load protected route data' }, 403)
  })

  await page.goto('/runtime')
  await expect(page.getByRole('heading', { level: 1, name: '无权访问此页面' })).toBeVisible()
})
