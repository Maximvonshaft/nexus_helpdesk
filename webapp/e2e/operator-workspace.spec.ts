import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const SCOPE_KEY = 'nexus-operator-workspace-scope'

type Scenario = 'handoff' | 'dispatch'

function user() {
  return {
    id: 9,
    username: 'operator',
    display_name: 'Operations Agent',
    role: 'agent',
    capabilities: [
      'ticket.read',
      'operator_queue.read',
      'outbound.send',
      'webchat.handoff.accept',
      'tool:speedaf.work_order.create:write',
    ],
  }
}

function queueResponse(scenario: Scenario) {
  if (scenario === 'dispatch') {
    return {
      items: [{
        queue_id: 'dispatch:72',
        case_key: 'ticket:42',
        source_type: 'dispatch',
        source_id: 72,
        ticket_id: 42,
        conversation_id: null,
        country_code: 'CH',
        channel_key: 'whatsapp',
        state: 'active',
        source_status: 'dead_letter',
        reopened: true,
        priority: 'urgent',
        owner: { kind: 'unassigned', user_id: null, team_id: null },
        sla: { state: 'breached', due_at: '2026-07-12T18:00:00Z', seconds_remaining: -1800 },
        retry: { state: 'exhausted', attempt_count: 5, max_attempts: 5, next_retry_at: null, error_category: 'routing_unavailable' },
        created_at: '2026-07-12T17:00:00Z',
        updated_at: '2026-07-12T18:30:00Z',
        source_links: {
          ticket: '/api/tickets/42',
          conversation: null,
          handoff: null,
          dispatch: null,
        },
      }],
      next_cursor: null,
      scope: { tenant_hash: 'hash', country_code: 'CH', channel_key: 'whatsapp' },
      filters: { state: 'active', source_type: 'dispatch', owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
    }
  }
  return {
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
      sla: { state: 'at_risk', due_at: '2026-07-12T22:00:00Z', seconds_remaining: 900 },
      retry: { state: 'not_applicable', attempt_count: 0, max_attempts: 0, next_retry_at: null, error_category: null },
      created_at: '2026-07-12T20:00:00Z',
      updated_at: '2026-07-12T20:30:00Z',
      source_links: {
        ticket: '/api/tickets/11',
        conversation: '/api/webchat/admin/tickets/11/thread',
        handoff: '/api/webchat/admin/handoff/queue',
        dispatch: null,
      },
    }],
    next_cursor: null,
    scope: { tenant_hash: 'hash', country_code: 'CH', channel_key: 'webchat' },
    filters: { state: 'active', source_type: null, owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
  }
}

function defaultThreadMessages() {
  return [
    { id: 1, direction: 'visitor', body: 'Where is parcel?', body_text: 'Where is parcel?', delivery_status: 'sent', created_at: '2026-07-12T20:00:00Z' },
    { id: 2, direction: 'agent', body: 'We are checking.', body_text: 'We are checking.', delivery_status: 'failed', author_label: 'Operations Agent', created_at: '2026-07-12T20:01:00Z' },
  ]
}

function threadResponse(messages?: ReturnType<typeof defaultThreadMessages>) {
  return {
    conversation_id: 'conv-1',
    ticket_id: 11,
    ticket_no: 'T-11',
    status: 'in_progress',
    conversation_state: 'human_review_required',
    required_action: '核实运单后回复客户',
    visitor: { name: 'Customer', email: null, phone: '+41790000000', ref: 'customer-1' },
    messages: messages ?? defaultThreadMessages(),
    actions: [],
    ai_turns: [],
    events: [],
    handoff: {
      id: 21,
      ticket_id: 11,
      status: 'requested',
      reason_text: 'Customer asked for a human',
      recommended_agent_action: 'Verify parcel evidence',
      waiting_seconds: 300,
      can_accept: true,
      can_decline: false,
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
      customer_request: 'Where is parcel?',
      required_action: '核实运单后回复客户',
      missing_fields: ['tracking_number'],
      tracking: { present: false },
      ai_state: {},
      evidence_summary: { outbound_messages: 1 },
      evidence_timeline: [{
        kind: 'outbound',
        label: 'web_chat',
        status: 'failed',
        summary: { delivery_status: 'failed', failure_code: 'provider_unavailable' },
        created_at: '2026-07-12T20:01:00Z',
        source_id: 'outbound:1',
      }],
      next_actions: [{ key: 'collect_missing_fields', label: 'Collect tracking number', tone: 'warning' }],
    },
    unread_count: 1,
    marked_unread: false,
  }
}

async function mockWorkspace(page: Page, scenario: Scenario, overrides?: { queue?: () => ReturnType<typeof queueResponse>; thread?: () => ReturnType<typeof threadResponse> }) {
  const channelKey = scenario === 'dispatch' ? 'whatsapp' : 'webchat'
  await page.addInitScript(([tokenKey, scopeKey, channel]) => {
    sessionStorage.setItem(tokenKey, 'operator-token')
    sessionStorage.setItem(scopeKey, JSON.stringify({ tenantKey: 'default', countryCode: 'CH', channelKey: channel }))
  }, [TOKEN_KEY, SCOPE_KEY, channelKey])

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(body),
    })
    if (url.pathname === '/api/auth/me') return json(user())
    if (url.pathname === '/api/admin/operator-queue/my-scopes') {
      return json({
        items: [{
          tenant_key: 'default',
          tenant_hash: 'hash',
          country_code: 'CH',
          channel_key: channelKey,
        }],
        requires_explicit_admin_scope: false,
      })
    }
    if (url.pathname === '/api/admin/operator-queue/unified') {
      expect(route.request().headers()['x-nexus-tenant']).toBe('default')
      expect(url.searchParams.get('country_code')).toBe('CH')
      expect(url.searchParams.get('channel_key')).toBe(channelKey)
      return json(overrides?.queue?.() ?? queueResponse(scenario))
    }
    if (url.pathname === '/api/webchat/admin/tickets/11/thread') return json(overrides?.thread?.() ?? threadResponse())
    if (url.pathname === '/api/tickets/42') {
      return json({ id: 42, ticket_no: 'T-42', title: 'WhatsApp dispatch repair', status: 'in_progress', priority: 'urgent' })
    }
    return route.fulfill({
      status: 404,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify({ detail: `Unhandled test API ${url.pathname}` }),
    })
  })
}

test('375px workspace keeps all four task surfaces reachable and explains disabled actions', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockWorkspace(page, 'handoff')
  await page.goto('/workspace')

  await expect(page.getByTestId('operator-workspace')).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  for (const label of ['队列', '案例', '沟通', '动作']) {
    const button = page.getByRole('button', { name: label, exact: true }).first()
    await expect(button).toBeVisible()
    expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  }

  await page.getByRole('button', { name: '动作', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '下一步动作' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('workspace-actions')
  await page.getByLabel('选择动作').selectOption('work_order')
  await expect(page.getByText(/不可执行原因：缺少运单/)).toBeVisible()

  await page.getByRole('button', { name: '沟通', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '客户沟通' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('workspace-conversation')
  await expect(page.getByText('发送失败', { exact: true }).first()).toBeVisible()
})

test('dispatch-only work remains actionable without fabricating a conversation or closure', async ({ page }) => {
  await mockWorkspace(page, 'dispatch')
  await page.goto('/workspace')

  const queueRow = page.getByRole('button', { name: /ticket:42/ })
  const caseStatus = page.getByLabel('案例状态')
  await expect(queueRow).toBeVisible()
  await expect(queueRow.getByText('重试已耗尽')).toBeVisible()
  await expect(caseStatus.getByText('SLA 已超时')).toBeVisible()
  await expect(caseStatus.getByText('已重新打开')).toBeVisible()
  await expect(page.getByRole('heading', { name: '来源记录摘要' })).toBeVisible()
  await expect(page.getByText('当前案例没有可用会话').first()).toBeVisible()
  await expect(page.locator('.operator-blocker').getByText('尚不能判定安全结案')).toBeVisible()
  await expect(page.locator('.operator-outcome-list').getByText('业务结果已确认')).toHaveCount(0)
})

test('operator navigation hides management surfaces that the current capability set cannot use', async ({ page }) => {
  await mockWorkspace(page, 'handoff')
  await page.goto('/workspace')

  const navigation = page.getByRole('navigation', { name: '主导航' })
  await expect(navigation.getByRole('link', { name: '工作台' })).toBeVisible()
  await expect(navigation.getByRole('link', { name: '知识' })).toHaveCount(0)
  await expect(navigation.getByRole('link', { name: '渠道管理' })).toHaveCount(0)
  await expect(navigation.getByRole('link', { name: '运行与审计' })).toHaveCount(0)
})

test('workspace preserves historical scroll position and exposes a bounded new-message action', async ({ page }) => {
  const messages = Array.from({ length: 36 }, (_, index) => ({
    id: index + 1,
    direction: index % 3 === 0 ? 'visitor' : 'agent',
    body: `History message ${index + 1} `.repeat(8),
    body_text: `History message ${index + 1} `.repeat(8),
    delivery_status: 'sent',
    created_at: `2026-07-12T20:${String(index).padStart(2, '0')}:00Z`,
  }))
  await mockWorkspace(page, 'handoff', { thread: () => threadResponse(messages) })
  await page.goto('/workspace')

  const timeline = page.locator('.operator-messages')
  await expect(timeline).toBeVisible()
  await expect.poll(() => timeline.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true)
  await expect.poll(() => timeline.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight <= 4)).toBe(true)

  await timeline.evaluate((node) => {
    node.scrollTop = 0
    node.dispatchEvent(new Event('scroll'))
  })
  messages.push({
    id: 100,
    direction: 'visitor',
    body: 'A new message while the operator reads history',
    body_text: 'A new message while the operator reads history',
    delivery_status: 'sent',
    created_at: '2026-07-12T21:00:00Z',
  })

  await expect(page.getByLabel('客户沟通').getByText('A new message while the operator reads history')).toBeVisible({ timeout: 7000 })
  expect(await timeline.evaluate((node) => node.scrollTop)).toBeLessThan(20)
  const newMessages = page.getByRole('button', { name: '1 条新消息，查看最新' })
  await expect(newMessages).toBeVisible()
  await newMessages.click()
  await expect.poll(() => timeline.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight <= 4)).toBe(true)
  await expect(newMessages).toHaveCount(0)
})

test('workspace protects an unsent reply before unload and case replacement', async ({ page }) => {
  const first = queueResponse('handoff')
  const second = {
    ...first.items[0],
    queue_id: 'handoff:22',
    case_key: 'ticket:12',
    source_id: 22,
    ticket_id: 12,
    updated_at: '2026-07-12T20:31:00Z',
  }
  await mockWorkspace(page, 'handoff', { queue: () => ({ ...first, items: [first.items[0], second] }) })
  await page.goto('/workspace')
  await page.getByLabel('回复客户').fill('Draft that must not be lost')

  await expect.poll(() => page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true })
    return window.dispatchEvent(event)
  })).toBe(false)

  await page.getByRole('button', { name: /ticket:12/ }).click()
  const discard = page.getByRole('dialog', { name: '放弃未发送的回复？' })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: '取消' }).click()
  await expect(discard).toHaveCount(0)
  await expect(page.getByRole('button', { name: /ticket:11/ })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByLabel('回复客户')).toHaveValue('Draft that must not be lost')
})

test('queue polling preserves a dirty reply when the selected task leaves the queue', async ({ page }) => {
  await page.clock.install()
  const first = queueResponse('handoff')
  const second = {
    ...first.items[0],
    queue_id: 'handoff:22',
    case_key: 'ticket:12',
    source_id: 22,
    ticket_id: 12,
    updated_at: '2026-07-12T20:31:00Z',
  }
  let queueItems = [first.items[0], second]
  let queueCalls = 0
  await mockWorkspace(page, 'handoff', {
    queue: () => {
      queueCalls += 1
      return { ...first, items: queueItems }
    },
  })
  await page.goto('/workspace')
  const reply = page.getByLabel('回复客户')
  await reply.fill('Draft retained while queue authority changes')

  queueItems = [second]
  await page.clock.fastForward(16_000)
  await expect.poll(() => queueCalls).toBeGreaterThan(1)

  await expect(page.getByText('当前任务已离开队列，回复草稿仍已保留')).toBeVisible()
  await expect(reply).toHaveValue('Draft retained while queue authority changes')
  await expect(page.getByRole('button', { name: '发送回复' })).toBeDisabled()
  await expect(page.getByText('当前任务动作已暂停')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: 'ticket:11' })).toBeVisible()

  await page.getByRole('button', { name: /ticket:12/ }).click()
  const discard = page.getByRole('dialog', { name: '放弃未发送的回复？' })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: '取消' }).click()
  await expect(reply).toHaveValue('Draft retained while queue authority changes')
})

test('queue polling advances to a current task when no reply draft exists', async ({ page }) => {
  await page.clock.install()
  const first = queueResponse('handoff')
  const second = {
    ...first.items[0],
    queue_id: 'handoff:22',
    case_key: 'ticket:12',
    source_id: 22,
    ticket_id: 12,
    updated_at: '2026-07-12T20:31:00Z',
  }
  let queueItems = [first.items[0], second]
  let queueCalls = 0
  await mockWorkspace(page, 'handoff', {
    queue: () => {
      queueCalls += 1
      return { ...first, items: queueItems }
    },
  })
  await page.goto('/workspace')
  await expect(page.getByRole('heading', { level: 1, name: 'ticket:11' })).toBeVisible()

  queueItems = [second]
  await page.clock.fastForward(16_000)
  await expect.poll(() => queueCalls).toBeGreaterThan(1)

  await expect(page.getByRole('heading', { level: 1, name: 'ticket:12' })).toBeVisible()
  await expect(page.getByText('当前任务已离开队列，回复草稿仍已保留')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /ticket:12/ })).toHaveAttribute('aria-pressed', 'true')
})
