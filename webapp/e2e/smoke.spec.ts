import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

function authUser() {
  return {
    id: 1,
    username: 'admin',
    display_name: 'Admin User',
    role: 'admin',
    capabilities: [
      'ticket.read',
      'operator_queue.read',
      'runtime.manage',
      'audit.read',
      'channel_account.manage',
      'ai_config.read',
      'ai_config.manage',
      'ticket.assign',
      'user.manage',
      'tool:speedaf.work_order.create:write',
    ],
  }
}

function knowledgeItem(id: number, title: string, status = 'active') {
  return {
    id,
    item_key: `kb-${id}`,
    title,
    summary: '',
    status,
    source_type: 'manual',
    knowledge_kind: id === 2 ? 'policy' : 'business_fact',
    channel: null,
    audience_scope: 'customer',
    language: null,
    priority: id === 2 ? 20 : 100,
    fact_question: id === 2 ? 'Can I return my parcel?' : 'Where is my parcel?',
    fact_answer: id === 2
      ? 'Confirm the applicable return window and merchant policy before answering.'
      : 'Use the tracking tool before answering delivery status questions.',
    fact_aliases_json: [],
    fact_status: status === 'active' ? 'approved' : 'draft',
    answer_mode: 'guided_answer',
    draft_body: 'Knowledge draft',
    published_body: status === 'active' ? 'Knowledge draft' : null,
    published_version: status === 'active' ? 1 : 0,
  }
}

function agentControlSnapshot() {
  return {
    generated_at: Date.now() / 1000,
    tenant_key: 'default',
    scope: {
      environment: 'production',
      market_id: null,
      channel: 'webchat',
      language: null,
      case_type: null,
    },
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
    capabilities: {
      can_manage: true,
      can_deploy: true,
      playground_model_execution: true,
    },
  }
}

async function fulfillApi(route: Route) {
  const url = new URL(route.request().url())
  const path = url.pathname
  const json = (body: unknown, status = 200) => route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })

  if (path === '/api/auth/me') return json(authUser())
  if (path === '/api/agent-control/snapshot') return json(agentControlSnapshot())
  if (path === '/api/admin/operator-queue/my-scopes') {
    return json({
      items: [{ tenant_key: 'default', tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' }],
      requires_explicit_admin_scope: false,
    })
  }
  if (path === '/api/admin/operator-queue/unified') {
    return json({
      items: [{
        queue_id: 'handoff:21',
        case_key: 'ticket:11',
        source_type: 'handoff',
        source_id: 21,
        ticket_id: 11,
        conversation_id: 1,
        country_code: 'CH',
        channel_key: 'webchat',
        state: 'active',
        source_status: 'requested',
        reopened: false,
        priority: 'high',
        owner: { kind: 'unassigned', user_id: null, team_id: null },
        sla: { state: 'at_risk', due_at: '2026-07-04T08:30:00Z', seconds_remaining: 900 },
        retry: { state: 'not_applicable', attempt_count: 0, max_attempts: 0, next_retry_at: null, error_category: null },
        created_at: '2026-07-04T08:00:00Z',
        updated_at: '2026-07-04T08:10:00Z',
        source_links: {
          ticket: '/api/tickets/11',
          conversation: '/api/webchat/admin/tickets/11/thread',
          handoff: '/api/webchat/admin/handoff/queue',
          dispatch: null,
        },
      }],
      next_cursor: null,
      scope: { tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' },
      filters: { state: 'active', source_type: null, owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
    })
  }
  if (path === '/api/webchat/admin/tickets/11/thread') {
    return json({
      conversation_id: 'conv-1',
      ticket_id: 11,
      ticket_no: 'T-11',
      status: 'in_progress',
      conversation_state: 'human_review_required',
      required_action: '核实运单后回复客户',
      visitor: { name: 'WebChat Visitor', email: 'visitor@example.test', phone: '+41790000000', ref: 'visitor-1' },
      messages: [
        { id: 1, direction: 'visitor', body: 'Where is my parcel?', body_text: 'Where is my parcel?', delivery_status: 'sent', created_at: '2026-07-04T08:00:00Z' },
        { id: 2, direction: 'agent', body: 'We are checking.', body_text: 'We are checking.', delivery_status: 'queued', author_label: 'Admin User', created_at: '2026-07-04T08:01:00Z' },
      ],
      actions: [],
      ai_turns: [],
      events: [],
      handoff: {
        id: 21,
        ticket_id: 11,
        status: 'requested',
        reason_text: 'Customer requested a human',
        recommended_agent_action: 'Review evidence and reply',
        waiting_seconds: 240,
        can_accept: true,
        can_decline: true,
        can_force_takeover: true,
        can_release: false,
        can_resume_ai: true,
        can_reply: false,
      },
      support_memory: {
        source: 'derived_support_memory_ledger',
        ticket: { id: 11, ticket_no: 'T-11', status: 'in_progress', country_code: 'CH' },
        conversation: { id: 'conv-1', status: 'open', channel_key: 'webchat' },
        current_intent: 'tracking_status',
        customer_request: 'Where is my parcel?',
        required_action: '核实运单后回复客户',
        missing_fields: ['tracking_number'],
        tracking: { present: false },
        ai_state: {},
        evidence_summary: { outbound_messages: 1 },
        evidence_timeline: [{
          kind: 'outbound',
          label: 'web_chat',
          status: 'queued',
          summary: { delivery_status: 'queued', provider_status: 'webchat_agent_reply_queued' },
          created_at: '2026-07-04T08:01:00Z',
          source_id: 'outbound:1',
        }],
        next_actions: [{ key: 'collect_missing_fields', label: 'Collect missing fields before customer-facing resolution', tone: 'warning' }],
      },
      unread_count: 1,
      marked_unread: false,
    })
  }
  if (path === '/api/lite/knowledge-studio') {
    return json({ kpis: [{ key: 'published', label: '已上线', value: 1, hint: '', tone: 'success' }] })
  }
  if (path === '/api/knowledge-items' && route.request().method() === 'GET') {
    return json({ items: [knowledgeItem(1, 'Delivery status'), knowledgeItem(2, 'Return policy', 'draft')], total: 2 })
  }
  if (path === '/api/admin/channel-accounts') {
    return json([
      { id: 7, provider: 'whatsapp', account_id: 'disabled-history', display_name: 'Disabled history', is_active: false, priority: 10, health_status: 'disabled', updated_at: '2026-07-04T08:00:00Z' },
      { id: 8, provider: 'whatsapp', account_id: 'wa-test', display_name: 'WhatsApp 主线路', is_active: true, priority: 10, health_status: 'offline', updated_at: '2026-07-04T08:00:00Z' },
    ])
  }
  if (path === '/api/admin/whatsapp/accounts/wa-test/status') {
    return json({
      account_id: 'wa-test',
      status: 'disconnected',
      qr_status: 'linked',
      phone_number: '+41790000000',
      reconnect_count: 0,
      channel_account_id: 8,
      channel_health_status: 'offline',
    })
  }
  if (path === '/api/admin/provider-runtime/status') {
    return json({
      ok: true,
      status: 'ready',
      app_env: 'test',
      webchat_runtime_enabled: true,
      configured_provider: 'private_ai_runtime',
      fallback_provider: null,
      warnings: [],
      boundary: {},
      providers: [{ name: 'private_ai_runtime', selected: true, configured: true, diagnostics: { direct_model: 'ci-direct-model' } }],
    })
  }
  if (path === '/api/support/conversations/metrics') {
    return json({ total: 1, needs_human: 0, ai_active: 1, by_channel: { webchat: 1 } })
  }
  if (path === '/api/lite/control-tower') {
    return json({
      generated_at: '2026-07-04T08:00:00Z',
      role: 'admin',
      user_id: 1,
      capabilities: authUser().capabilities,
      kpis: [{ key: 'unassigned', label: '未分配', value: 1, hint: '需要处理', tone: 'warning' }],
      manager_actions: [{ key: 'assign', label: '调度未分配队列', count: 1, tone: 'warning', next: '进入工作台', href: '/workspace', capability: 'ticket.assign', enabled: true }],
      team_workload: [],
      channel_health: [],
      bulletin_impact: [],
      governance_lanes: [],
      template_blocks: [],
      facts: {},
    })
  }
  if (path === '/api/tickets/11/speedaf/work-orders') {
    return json({ ok: true, status: 'queued', message: 'Speedaf work order queued.', jobId: 91, dedupeKey: 'bounded-test-key' })
  }
  if (path.startsWith('/api/webchat/admin/handoff/')) return json({ id: 21, status: 'accepted' })

  return json({ detail: `Unhandled mock for ${path}` }, 404)
}

async function mockAuthenticatedConsole(page: Page) {
  await page.addInitScript(([storageKey, token]) => {
    window.sessionStorage.setItem(storageKey, token)
  }, [TOKEN_KEY, 'admin-token'])
  await page.route('**/api/**', fulfillApi)
}

async function openKnowledge(page: Page) {
  await page.goto('/knowledge')
  await expect(page.getByRole('heading', { level: 1, name: '知识与流程' })).toBeVisible()
}

test('login page renders', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { level: 1, name: '登录' })).toBeVisible()
  await expect(page.getByLabel('账号')).toBeVisible()
  await expect(page.getByRole('button', { name: '登录' })).toBeVisible()
})

test('unauthenticated protected route redirects back to login', async ({ page }) => {
  await page.goto('/webchat')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByText('请勿在共享设备保存密码。')).toBeVisible()
})

test('canonical workspace renders the unified queue, evidence, and delivery truth', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.goto('/workspace')

  await expect(page.getByTestId('operator-workspace')).toBeVisible()
  await expect(page.getByRole('heading', { name: '已知信息' })).toBeVisible()
  const queueRow = page.getByRole('button', { name: /ticket:11/ })
  await expect(queueRow).toBeVisible()
  await expect(page.getByText('即将超时', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('客户通知', { exact: true }).first()).toBeVisible()
  await expect(page.getByLabel('送达状态').getByText('等待发送')).toBeVisible()
  await expect(page.getByText('服务端最终授权')).toHaveCount(0)
  await expect(page.getByText('业务结果已确认')).toHaveCount(0)
})

test('legacy support entry redirects into the canonical workspace', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.goto('/webchat')
  await expect(page).toHaveURL(/\/workspace$/)
  await expect(page.getByTestId('operator-workspace')).toBeVisible()
})

test('canonical supporting routes render in one application shell', async ({ page }) => {
  await mockAuthenticatedConsole(page)

  await openKnowledge(page)
  await expect(page.getByRole('button', { name: /Delivery status/ })).toBeVisible()

  await page.goto('/agent-control')
  await expect(page.getByRole('heading', { level: 1, name: '自动处理配置' })).toBeVisible()
  await expect(page.getByText('当前范围尚未配置已发布版本。')).toBeVisible()

  await page.goto('/channels')
  await expect(page.getByRole('heading', { level: 1, name: '渠道管理' })).toBeVisible()
  await expect(page.getByText('WhatsApp 主线路')).toBeVisible()
  await expect(page.getByText('Disabled history')).toHaveCount(0)

  await page.goto('/runtime')
  await expect(page.getByRole('heading', { level: 1, name: '系统运行' })).toBeVisible()
  await expect(page.getByText('处理方式').locator('..')).toContainText('自动处理')

  await page.goto('/control-tower')
  await expect(page.getByRole('heading', { level: 1, name: '运营监控' })).toBeVisible()
})

test('runtime failure never presents normal operation', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.route('**/api/admin/provider-runtime/status', (route) => route.fulfill({
    status: 503,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({ detail: 'runtime unavailable' }),
  }))
  await page.goto('/runtime')
  await expect(page.getByRole('alert').filter({ hasText: '无法读取系统状态' })).toBeVisible()
  await expect(page.getByText('无运行提醒')).toHaveCount(0)
})

test('queued controlled action remains pending and hides technical id by default', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.goto('/workspace')

  await page.getByRole('combobox', { name: '选择操作' }).click()
  await page.getByRole('option', { name: '创建催派工单' }).click()
  await page.getByRole('textbox', { name: '运单', exact: true }).fill('WB123456')
  await page.getByRole('textbox', { name: '客户电话', exact: true }).fill('+41790000000')
  await page.getByRole('textbox', { name: '催派说明', exact: true }).fill('Follow up delivery')
  await page.getByRole('button', { name: '创建催派工单' }).click()

  const result = page.getByRole('status').filter({ hasText: '请求已排队' })
  await expect(result).toBeVisible()
  await expect(page.getByText('#91', { exact: true })).not.toBeVisible()
  await result.getByRole('button', { name: '处理编号' }).click()
  await expect(page.getByText('#91', { exact: true })).toBeVisible()
})

test('mobile workspace navigation and primary controls meet the 44px target floor', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockAuthenticatedConsole(page)
  await page.goto('/workspace')

  const mobileTabs = page.getByRole('tab')
  await expect(mobileTabs.first()).toBeVisible()
  for (let index = 0; index < await mobileTabs.count(); index += 1) {
    expect((await mobileTabs.nth(index).boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  expect((await page.getByRole('button', { name: '退出' }).boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
})

test('knowledge editing protects drafts and requires an explicit publication review', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await openKnowledge(page)

  const title = page.getByRole('textbox', { name: '知识标题', exact: true })
  await expect(title).toHaveValue('Delivery status')
  await title.fill('Edited delivery status')

  await page.getByRole('button', { name: /Return policy/ }).click()
  const discard = page.getByRole('dialog', { name: '放弃未保存的修改？' })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: '放弃修改' }).click()
  await expect(title).toHaveValue('Return policy')

  await title.fill('Return policy — reviewed')
  await page.getByRole('button', { name: '发布', exact: true }).click()
  const review = page.getByRole('dialog', { name: '发布知识' })
  await expect(review).toBeVisible()
  await expect(review.getByText('Return policy — reviewed')).toBeVisible()
  await expect(review.getByText('Can I return my parcel?')).toBeVisible()
  await expect(review.getByText('提交后请等待发布状态更新；提交成功不等于已经同步完成。')).toBeVisible()
})
