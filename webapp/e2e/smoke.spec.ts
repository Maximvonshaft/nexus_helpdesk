import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

function authUser() {
  return {
    id: 1,
    username: 'admin',
    display_name: 'Admin User',
    role: 'admin',
    capabilities: ['ticket.read', 'runtime.manage', 'channel_account.manage', 'ai_config.read', 'ai_config.manage'],
  }
}

async function fulfillApi(route: Route) {
  const url = new URL(route.request().url())
  const path = url.pathname
  const json = (body: unknown) => route.fulfill({
    status: 200,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })

  if (path === '/api/auth/me') return json(authUser())
  if (path === '/api/support/conversations') {
    return json({
      source: 'nexus_support_conversations',
      view: url.searchParams.get('view') || 'open',
      items: [
        {
          session_key: 'webchat:conv-1',
          conversation_id: 'conv-1',
          channel: 'webchat',
          source: 'webchat',
          ticket_id: 11,
          ticket_no: 'T-11',
          title: 'WebChat visitor',
          status: 'open',
          conversation_state: 'ai_active',
          display_name: 'WebChat Visitor',
          customer_contact: 'visitor@example.test',
          updated_at: '2026-07-04T08:00:00Z',
          latest_message: 'hello',
          latest_author: 'customer',
          needs_human: false,
          handoff_status: 'none',
          ai_status: 'private_ai_runtime',
          ai_suspended: false,
          tracking_number_present: false,
          can_force_takeover: true,
          can_accept: false,
          can_release: false,
          can_resume_ai: false,
          can_reply: true,
        },
      ],
    })
  }
  if (path === '/api/support/conversations/detail') {
    return json({
      source: 'nexus_support_conversations',
      conversation: {
        session_key: 'webchat:conv-1',
        conversation_id: 'conv-1',
        channel: 'webchat',
        ticket_id: 11,
        ticket_no: 'T-11',
        title: 'WebChat visitor',
        status: 'open',
        conversation_state: 'ai_active',
        display_name: 'WebChat Visitor',
        customer_contact: 'visitor@example.test',
        needs_human: false,
        handoff_status: 'none',
        ai_status: 'private_ai_runtime',
        ai_suspended: false,
        tracking_number_present: false,
        can_force_takeover: true,
        can_reply: true,
      },
      ticket: {
        id: 11,
        ticket_no: 'T-11',
        status: 'open',
        priority: 'normal',
        tracking_number_present: false,
      },
      messages: [
        { id: 'm-1', author: 'customer', body: 'hello', timestamp: '2026-07-04T08:00:00Z' },
        { id: 'm-2', author: 'ai', body: 'Hello, how can I assist you today?', timestamp: '2026-07-04T08:00:01Z' },
      ],
      support_memory: {
        source: 'derived_support_memory_ledger',
        ticket: { id: 11, ticket_no: 'T-11', status: 'open' },
        conversation: { id: 'conv-1', status: 'open', channel_key: 'webchat' },
        missing_fields: [],
        tracking: { present: false },
        ai_state: {},
        evidence_summary: {},
        evidence_timeline: [],
        next_actions: [],
      },
    })
  }
  if (path === '/api/support/conversations/state') {
    return json({
      source: 'nexus_support_conversations',
      open: 1,
      requested_handoffs: 0,
      my_handoffs: 0,
      generated_at: '2026-07-04T08:00:00Z',
    })
  }
  if (path === '/api/support/conversations/metrics') {
    return json({
      source: 'nexus_support_conversations',
      since_hours: 24,
      total: 1,
      needs_human: 0,
      ai_active: 1,
      by_channel: { webchat: 1 },
      by_state: { ai_active: 1 },
    })
  }
  if (path === '/api/lite/knowledge-studio') {
    return json({
      generated_at: '2026-07-04T08:00:00Z',
      role: 'admin',
      user_id: 1,
      capabilities: [],
      kpis: [{ key: 'published', label: '已发布', value: 2, hint: '', tone: 'success' }],
      items: [{
        id: 1,
        item_key: 'kb-1',
        title: 'Delivery status',
        status: 'published',
        source_type: 'manual',
        knowledge_kind: 'support',
        audience_scope: 'customer',
        priority: 100,
        parsing_status: 'ready',
        fact_status: 'ready',
        answer_mode: 'runtime_context',
        published_version: 1,
        indexed_version: 1,
        chunk_count: 3,
        draft_ready: true,
        publish_ready: true,
        retrieval_test_ready: true,
        has_conflict: false,
        updated_at: '2026-07-04T08:00:00Z',
        href: '#',
        evidence: 'ok',
      }],
      conflicts: [],
      release_lifecycle: [],
      template_blocks: [],
      facts: {},
    })
  }
  if (path === '/api/knowledge-items') {
    return json({
      total: 1,
      limit: 20,
      offset: 0,
      items: [{
        id: 1,
        item_key: 'kb-1',
        title: 'Delivery status',
        status: 'active',
        source_type: 'manual',
        knowledge_kind: 'business_fact',
        audience_scope: 'customer',
        priority: 100,
        parsing_status: 'ready',
        fact_status: 'approved',
        answer_mode: 'runtime_context',
        published_version: 1,
        indexed_version: 1,
        chunk_count: 3,
        draft_ready: true,
        publish_ready: true,
        retrieval_test_ready: true,
        has_conflict: false,
        updated_at: '2026-07-04T08:00:00Z',
        href: '#',
        evidence: 'ok',
        fact_question: 'Where is my parcel?',
        fact_answer: 'Use the tracking tool before answering delivery status questions.',
      }],
    })
  }
  if (path === '/api/admin/channel-accounts') {
    return json([
      {
        id: 7,
        provider: 'whatsapp',
        account_id: 'default',
        display_name: 'WhatsApp Default (disabled history)',
        is_active: false,
        priority: 10,
        health_status: 'disabled',
        updated_at: '2026-07-04T08:00:00Z',
      },
      {
        id: 8,
        provider: 'whatsapp',
        account_id: 'wa-test-41798559737',
        display_name: 'WhatsApp Native +41798559737',
        is_active: true,
        priority: 10,
        health_status: 'offline',
        updated_at: '2026-07-04T08:00:00Z',
      },
    ])
  }
  if (path === '/api/admin/whatsapp/accounts/wa-test-41798559737/status') {
    return json({
      account_id: 'wa-test-41798559737',
      status: 'disconnected',
      qr_status: 'linked',
      phone_number: '+41790000000',
      reconnect_count: 0,
      channel_account_id: 8,
      channel_health_status: 'offline',
    })
  }
  if (path === '/api/admin/external_channel/runtime-health') {
    return json({
      stale_link_count: 0,
      pending_sync_jobs: 0,
      dead_sync_jobs: 0,
      pending_attachment_jobs: 0,
      dead_attachment_jobs: 0,
      external_dead_outbound: 0,
      warnings: [],
    })
  }
  if (path === '/api/admin/provider-runtime/status') {
    return json({
      ok: true,
      status: 'ready',
      fallback_provider: null,
      warnings: [],
      providers: [{
        name: 'private_ai_runtime',
        status: 'ready',
        ok: true,
        diagnostics: {
          direct_model: 'ci-direct-model',
          rag_model: 'ci-rag-model',
          chat_mode: 'direct',
          request_shape: 'responses',
          rag_runtime_isolated: true,
          allow_shared_rag_model: false,
        },
      }],
    })
  }

  return route.fulfill({
    status: 404,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({ detail: `Unhandled mock for ${path}` }),
  })
}

async function mockAuthenticatedConsole(page: Page) {
  await page.addInitScript(([storageKey, token]) => {
    window.sessionStorage.setItem(storageKey, token)
  }, [TOKEN_KEY, 'admin-token'])
  await page.route('**/api/**', fulfillApi)
}

test('login page renders', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { level: 1, name: '进入运营工作台' })).toBeVisible()
  await expect(page.getByLabel('账号')).toBeVisible()
  await expect(page.getByRole('button', { name: '登录运营工作台' })).toBeVisible()
})

test('unauthenticated protected route redirects back to login', async ({ page }) => {
  await page.goto('/webchat')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByText('登录状态只保存在当前浏览器会话中。')).toBeVisible()
})

test('deleted legacy routes fall back to the support workbench boundary', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.goto('/workspace')

  await expect(page.getByTestId('legacy-route-retired')).toBeVisible()
  await expect(page.getByRole('heading', { name: '旧入口已下线' })).toBeVisible()
  await page.getByRole('link', { name: '进入客服工作台' }).click()
  await expect(page).toHaveURL(/\/webchat(?:\?.*)?$/)
})

test('support workbench renders the consolidated production views', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.goto('/webchat')

  await expect(page.getByTestId('nexus-support-console')).toBeVisible()
  await expect(page.getByRole('heading', { name: '客服工作台' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'WebChat Visitor' })).toBeVisible()
  await expect(page.getByText('Hello, how can I assist you today?')).toBeVisible()

  await page.getByRole('button', { name: '知识' }).click()
  await expect(page.getByRole('button', { name: /Delivery status/ })).toBeVisible()
  await expect(page.getByText('会话状态暂停刷新', { exact: true })).toBeVisible()
  await expect(page.getByText('1 个打开会话', { exact: true })).toHaveCount(0)

  await page.getByRole('button', { name: '渠道' }).click()
  await expect(page.getByText('WhatsApp Native +41798559737')).toBeVisible()
  await expect(page.getByText('WhatsApp Default (disabled history)')).toHaveCount(0)
  const disconnected = page.getByText('disconnected', { exact: true })
  await expect(disconnected).toBeVisible()
  await expect(disconnected).toHaveClass(/danger/)
  await expect(page.getByRole('table', { name: '当前启用的渠道账号' })).toBeVisible()

  await page.getByRole('button', { name: '运行' }).click()
  await expect(page.getByText('AI Runtime')).toBeVisible()
  await expect(page.getByText('正常')).toBeVisible()
})


test('runtime failure never presents normal operation', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.route('**/api/admin/provider-runtime/status', (route) => route.fulfill({
    status: 503,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({ detail: 'runtime unavailable' }),
  }))
  await page.goto('/webchat')
  await page.getByRole('button', { name: '运行' }).click()
  await expect(page.getByText('不可用', { exact: true })).toBeVisible()
  await expect(page.getByText('正常', { exact: true })).toHaveCount(0)
})

test('queued controlled action remains pending and hides technical id by default', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.route('**/api/tickets/11/speedaf/work-orders', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({
      ok: true,
      status: 'queued',
      message: 'Speedaf work order queued.',
      jobId: 91,
      dedupeKey: 'bounded-test-key',
    }),
  }))
  await page.goto('/webchat')
  await page.getByLabel('运单').fill('WB123456')
  await page.getByLabel('Caller ID').fill('+41790000000')
  await page.getByLabel('说明').fill('Follow up delivery')
  await page.getByRole('button', { name: '创建工单' }).click()

  const result = page.locator('.support-action-result').filter({ hasText: '请求已排队' })
  await expect(result).toBeVisible()
  await expect(result).not.toHaveClass(/success/)
  await expect(page.getByText('Job #91')).not.toBeVisible()
  await result.getByText('技术详情').click()
  await expect(page.getByText('Job #91')).toBeVisible()
})

test('mobile navigation and segment controls meet the 44px target floor', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockAuthenticatedConsole(page)
  await page.goto('/webchat')

  const topTab = page.getByTestId('support-workbench-tabs').getByRole('button').first()
  const segment = page.locator('.support-segments').first().getByRole('button').first()
  expect((await topTab.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  expect((await segment.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)

  await page.getByRole('button', { name: /WebChat Visitor/ }).click()
  const back = page.getByRole('button', { name: '‹ 会话' })
  expect((await back.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
})
