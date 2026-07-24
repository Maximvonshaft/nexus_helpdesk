import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

const responsiveUser = {
  id: 1,
  username: 'responsive-admin',
  display_name: 'Responsive Operations Administrator',
  email: 'responsive@example.test',
  role: 'admin',
  team_id: null,
  must_change_password: false,
  mfa_enabled: false,
  last_login_at: '2026-07-24T05:00:00Z',
  password_changed_at: '2026-07-20T05:00:00Z',
  capabilities: [
    'ticket.read',
    'ticket.assign',
    'operator_queue.read',
    'webchat.handoff.accept',
    'webcall.voice.read',
    'webcall.voice.queue.view',
    'webcall.voice.accept',
    'webcall.voice.reject',
    'webcall.voice.end',
    'webcall.voice.control',
    'ai_config.read',
    'ai_config.manage',
    'channel_account.manage',
    'runtime.manage',
    'audit.read',
    'security.read',
    'user.manage',
    'market.manage',
  ],
}

const responsiveAgentState = {
  user_id: 1,
  status: 'online',
  heartbeat_fresh: true,
  assignable: true,
  max_concurrent_conversations: 4,
  active_conversations: 1,
  available_capacity: 3,
  voice_enabled: true,
  voice_assignable: true,
  max_concurrent_voice_calls: 1,
  active_voice_calls: 0,
  reserved_voice_offers: 0,
  available_voice_capacity: 1,
  voice_wrap_up_seconds: 30,
  last_heartbeat_at: '2026-07-24T05:00:00Z',
  heartbeat_ttl_seconds: 90,
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

async function fulfillResponsiveApi(route: Route) {
  const url = new URL(route.request().url())
  const path = url.pathname

  if (path === '/api/auth/me') return json(route, responsiveUser)
  if (path === '/api/operator/agent-state') return json(route, responsiveAgentState)
  if (path === '/api/operator/agent-state/heartbeat') return json(route, responsiveAgentState)
  if (path === '/api/webchat/admin/voice/sessions') return json(route, { items: [] })
  if (path === '/api/admin/operator-queue/my-scopes') {
    return json(route, {
      items: [{ tenant_key: 'default', tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' }],
      requires_explicit_admin_scope: false,
    })
  }
  if (path === '/api/admin/operator-queue/unified') {
    return json(route, {
      items: [],
      next_cursor: null,
      scope: { tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' },
      filters: { state: 'active', source_type: null, owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
    })
  }
  if (path === '/api/agent-control/snapshot') {
    return json(route, {
      generated_at: Date.now() / 1000,
      tenant_key: 'default',
      scope: { environment: 'production', market_id: null, channel: 'webchat', language: null, case_type: null },
      definitions: [],
      releases: [],
      deployments: [],
      resolved_agent: null,
      resolved_agent_digest: null,
      resolution_error: 'agent_deployment_not_found',
      personas: [],
      persona_total: 0,
      knowledge: [],
      resources: [],
      resolved_playbooks: [],
      tools: [],
      tool_policies: [],
      integrations: [],
      capabilities: { can_manage: true, can_deploy: true, playground_model_execution: false },
    })
  }
  if (path === '/api/lite/knowledge-studio') return json(route, { kpis: [] })
  if (path === '/api/knowledge-items' && route.request().method() === 'GET') return json(route, { items: [], total: 0 })
  if (path === '/api/admin/channel-accounts') return json(route, [])
  if (path === '/api/admin/channel-onboarding-tasks') return json(route, { items: [], total: 0 })
  if (path === '/api/admin/provider-runtime/status') {
    return json(route, {
      ok: true,
      status: 'ready',
      app_env: 'test',
      webchat_runtime_enabled: false,
      configured_provider: null,
      fallback_provider: null,
      warnings: [],
      boundary: {},
      providers: [],
    })
  }
  if (path === '/api/support/conversations/metrics') {
    return json(route, { total: 0, needs_human: 0, ai_active: 0, by_channel: {}, runtime_latency: null })
  }
  if (path === '/api/lite/control-tower') {
    return json(route, {
      generated_at: '2026-07-24T05:00:00Z',
      role: 'admin',
      user_id: 1,
      capabilities: responsiveUser.capabilities,
      kpis: [],
      manager_actions: [],
      team_workload: [],
      channel_health: [],
      bulletin_impact: [],
      governance_lanes: [],
      template_blocks: [],
      facts: {},
    })
  }

  return json(route, { detail: `Unavailable responsive acceptance fixture: ${path}` }, 404)
}

async function mockResponsiveConsole(page: Page) {
  await page.addInitScript(([storageKey, token]) => {
    window.sessionStorage.setItem(storageKey, token)
  }, [TOKEN_KEY, 'responsive-admin-token'])
  await page.route('**/api/**', fulfillResponsiveApi)
}

const canonicalRoutes = [
  { path: '/workspace', ready: (page: Page) => page.getByTestId('operator-workspace') },
  { path: '/knowledge', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '知识与流程' }) },
  { path: '/agent-control', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '自动处理配置' }) },
  { path: '/channels', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '渠道管理' }) },
  { path: '/runtime', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '系统运行' }) },
  { path: '/control-tower', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '运营监控' }) },
  { path: '/administration', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '系统管理' }) },
  { path: '/account', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '账户设置' }) },
] as const

for (const viewport of [
  { width: 375, height: 812 },
  { width: 768, height: 1024 },
  { width: 1024, height: 900 },
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
  await expect(drawer.getByRole('checkbox', { name: '关闭电话接线' })).toBeVisible()

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
