import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const SCOPE_KEY = 'nexus-operator-workspace-scope'
const authUser = {
  id: 1, username: 'admin', display_name: '客服主管', role: 'admin',
  capabilities: ['ticket.read', 'operator_queue.read', 'runtime.manage', 'channel_account.manage', 'ai_config.read', 'ai_config.manage'],
}

async function mockApplication(page: Page) {
  await page.addInitScript(([tokenKey, scopeKey]) => {
    sessionStorage.setItem(tokenKey, 'admin-token')
    sessionStorage.setItem(scopeKey, JSON.stringify({ tenantKey: 'default', countryCode: 'CH', channelKey: 'webchat' }))
  }, [TOKEN_KEY, SCOPE_KEY])
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body) })
    if (path === '/api/auth/me') return json(authUser)
    if (path === '/api/admin/operator-queue/unified') return json({ items: [], next_cursor: null, scope: { tenant_hash: 'hash', country_code: 'CH', channel_key: 'webchat' }, filters: {} })
    if (path === '/api/knowledge-items') return json({ total: 1, items: [{ id: 1, item_key: 'delivery-delay', title: '包裹延迟', summary: '延迟查询流程', status: 'active', source_type: 'text', knowledge_kind: 'business_fact', audience_scope: 'customer', priority: 10, indexed_version: 1, chunk_count: 1, published_version: 1, created_at: '2026-07-14T00:00:00Z', updated_at: '2026-07-14T00:00:00Z', fact_question: '包裹为什么延迟？', fact_answer: '先查询最新轨迹和异常记录。' }] })
    if (path === '/api/admin/channel-accounts') return json([{ id: 8, provider: 'whatsapp', account_id: 'wa-main', display_name: 'WhatsApp 客服', is_active: true, priority: 10, health_status: 'connected', updated_at: '2026-07-14T00:00:00Z' }])
    if (path === '/api/admin/whatsapp/accounts/wa-main/status') return json({ account_id: 'wa-main', status: 'connected', qr_status: 'linked', phone_number: '+41790000000', reconnect_count: 0, channel_account_id: 8, channel_health_status: 'connected' })
    if (path === '/api/admin/provider-runtime/status') return json({ ok: true, status: 'ready', webchat_runtime_enabled: true, providers: [{ name: 'service-a', selected: true, feature_enabled: true, configured: true, runtime: 'private', capabilities: {} }], warnings: [], boundary: { secret_values_exposed: false, external_network_call: false, customer_message_sent: false } })
    return route.fulfill({ status: 404, contentType: 'application/json; charset=utf-8', body: JSON.stringify({ detail: path }) })
  })
}

test('one shell navigates across customer service, knowledge, channels and service assurance', async ({ page }) => {
  await mockApplication(page)
  await page.goto('/workspace')
  const nav = page.getByRole('navigation', { name: '主导航' })
  for (const label of ['客服工作台', '知识与规则', '渠道状态', '系统保障']) await expect(nav.getByRole('link', { name: new RegExp(label) })).toBeVisible()

  await nav.getByRole('link', { name: /知识与规则/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: '知识与规则' })).toBeVisible()
  await expect(page.getByText('包裹延迟')).toBeVisible()

  await page.getByRole('link', { name: /渠道状态/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: '渠道状态' })).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: 'WhatsApp 客服' })).toBeVisible()

  await page.getByRole('link', { name: /系统保障/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: '系统保障' })).toBeVisible()
  await expect(page.getByText('客户服务可以正常使用')).toBeVisible()
})

test('authenticated visible application contains no internal automation vocabulary', async ({ page }) => {
  await mockApplication(page)
  for (const path of ['/workspace', '/knowledge', '/channels', '/system']) {
    await page.goto(path)
    await expect(page.locator('body')).not.toContainText(/\b(?:AI|Artificial Intelligence|Runtime|Provider|RAG|Prompt|Model|Agent)\b/i)
  }
})
