import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const SCOPE_KEY = 'nexus-operator-workspace-scope'

type Scenario = 'handoff' | 'dispatch'

type EventPage = {
  events: Array<{
    id: number
    event_type: string
    payload_json: Record<string, unknown>
    created_at: string
  }>
  last_event_id: number
  has_more: boolean
  wait_ms: number
}

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
    message_page: { before_id: null, has_more: false, limit: 100 },
    actions: [],
    ai_turns: [],
    events: [],
    last_event_id: 0,
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

function emptyEventPage(afterId = 0): EventPage {
  return {
    events: [],
    last_event_id: afterId,
    has_more: false,
    wait_ms: 0,
  }
}

async function mockWorkspace(page: Page, scenario: Scenario, overrides?: {
  queue?: () => ReturnType<typeof queueResponse>
  thread?: (url: URL) => ReturnType<typeof threadResponse>
  events?: (afterId: number) => EventPage
}) {
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
    if (url.pathname === '/api/webchat/admin/tickets/11/thread') return json(overrides?.thread?.(url) ?? threadResponse())
    if (/^\/api\/webchat\/admin\/tickets\/\d+\/events$/.test(url.pathname)) {
      expect(url.searchParams.get('wait_ms')).toBe('0')
      const afterId = Number(url.searchParams.get('after_id') || 0)
      return json(overrides?.events?.(afterId) ?? emptyEventPage(afterId))
    }
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

test('375px workspace keeps all four task surfaces reachable and states missing prerequisites', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockWorkspace(page, 'handoff')
  await page.goto('/workspace')

  await expect(page.getByTestId('operator-workspace')).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  for (const label of ['待处理', '任务详情', '客户沟通', '操作']) {
    const tab = page.getByRole('tab', { name: label, exact: true }).first()
    await expect(tab).toBeVisible()
    expect((await tab.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  }

  await page.getByRole('tab', { name: '操作', exact: true }).click()
  await expect(page.getByRole('heading', { name: '下一步' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('workspace-actions')
  await page.getByRole('combobox', { name: '选择操作' }).click()
  await page.getByRole('option', { name: '创建催派工单' }).click()
  await expect(page.getByText('缺少运单', { exact: true })).toBeVisible()

  await page.getByRole('tab', { name: '客户沟通', exact: true }).click()
  await expect(page.getByRole('heading', { name: '客户沟通' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('workspace-conversation')
  await expect(page.getByText('发送失败', { exact: true }).first()).toBeVisible()
})

test('dispatch-only work remains actionable without fabricating a conversation or closure', async ({ page }) => {
  await mockWorkspace(page, 'dispatch')
  await page.goto('/workspace')

  const queueRow = page.getByRole('button', { name: /ticket:42/ })
  await expect(queueRow).toBeVisible()
  await expect(queueRow.getByText('自动重试失败')).toBeVisible()
  await expect(page.getByText('已超时', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('已重新打开', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('heading', { name: '任务摘要' })).toBeVisible()
  await expect(page.getByText('暂无客户沟通').first()).toBeVisible()
  await expect(page.getByText('服务端最终授权')).toHaveCount(0)
  await expect(page.getByText('业务结果已确认')).toHaveCount(0)
})

test('operator navigation hides management surfaces that the capability set cannot use', async ({ page }) => {
  await mockWorkspace(page, 'handoff')
  await page.goto('/workspace')

  const navigation = page.getByRole('navigation', { name: '主导航' })
  await expect(navigation.getByRole('link', { name: '案例处理' })).toBeVisible()
  await expect(navigation.getByRole('link', { name: '知识与流程' })).toHaveCount(0)
  await expect(navigation.getByRole('link', { name: '渠道管理' })).toHaveCount(0)
  await expect(navigation.getByRole('link', { name: '系统运行' })).toHaveCount(0)
  await expect(navigation.getByRole('link', { name: '运营监控' })).toHaveCount(0)
})

test('workspace loads older messages through the same bounded thread route', async ({ page }) => {
  const latest = [
    { id: 101, direction: 'visitor', body: 'Latest customer message', body_text: 'Latest customer message', delivery_status: 'sent', created_at: '2026-07-12T21:01:00Z' },
    { id: 102, direction: 'agent', body: 'Latest agent reply', body_text: 'Latest agent reply', delivery_status: 'sent', created_at: '2026-07-12T21:02:00Z' },
  ]
  const older = [
    { id: 99, direction: 'visitor', body: 'Older customer message', body_text: 'Older customer message', delivery_status: 'sent', created_at: '2026-07-12T20:59:00Z' },
    { id: 100, direction: 'agent', body: 'Older agent reply', body_text: 'Older agent reply', delivery_status: 'sent', created_at: '2026-07-12T21:00:00Z' },
  ]
  let historicalRequests = 0
  await mockWorkspace(page, 'handoff', {
    thread: (url) => {
      if (url.searchParams.get('before_message_id') === '101') {
        historicalRequests += 1
        return { ...threadResponse(older), message_page: { before_id: null, has_more: false, limit: 100 } }
      }
      return { ...threadResponse(latest), message_page: { before_id: 101, has_more: true, limit: 100 } }
    },
  })
  await page.goto('/workspace')

  await expect(page.getByText('Latest customer message')).toBeVisible()
  await page.getByRole('button', { name: '加载更早消息' }).click()
  await expect(page.getByText('Older customer message')).toBeVisible()
  await expect.poll(() => historicalRequests).toBe(1)
  await expect(page.getByRole('button', { name: '加载更早消息' })).toHaveCount(0)
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
  let eventDelivered = false
  await mockWorkspace(page, 'handoff', {
    thread: () => threadResponse(messages),
    events: (afterId) => {
      if (!eventDelivered && messages.some((message) => message.id === 100)) {
        eventDelivered = true
        return {
          events: [{
            id: 1,
            event_type: 'message.created',
            payload_json: { message_id: 100, direction: 'visitor' },
            created_at: '2026-07-12T21:00:00Z',
          }],
          last_event_id: 1,
          has_more: false,
          wait_ms: 0,
        }
      }
      return emptyEventPage(afterId)
    },
  })
  await page.goto('/workspace')

  const conversation = page.getByLabel('客户沟通')
  const timeline = conversation.locator('[aria-live="polite"]')
  await expect(timeline).toBeVisible()
  await expect.poll(() => timeline.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true)

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

  await expect(conversation.getByText('A new message while the operator reads history')).toBeVisible({ timeout: 10_000 })
  expect(await timeline.evaluate((node) => node.scrollTop)).toBeLessThan(20)
  const newMessages = page.getByRole('button', { name: '1 条新消息，查看最新' })
  await expect(newMessages).toBeVisible()
  await newMessages.click()
  await expect.poll(() => timeline.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight <= 4)).toBe(true)
  await expect(newMessages).toHaveCount(0)
})

test('workspace protects an unsent reply before unload and task replacement', async ({ page }) => {
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
  await discard.getByRole('button', { name: '继续编辑' }).click()
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

  await expect(page.getByText('任务已离开待处理列表').first()).toBeVisible()
  await expect(page.getByText('回复草稿已保留，操作已暂停。')).toBeVisible()
  await expect(reply).toHaveValue('Draft retained while queue authority changes')
  await expect(page.getByRole('button', { name: '发送回复' })).toBeDisabled()
  await expect(page.getByRole('heading', { level: 1, name: 'ticket:11' })).toBeVisible()

  await page.getByRole('button', { name: /ticket:12/ }).click()
  const discard = page.getByRole('dialog', { name: '放弃未发送的回复？' })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: '继续编辑' }).click()
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
  await expect(page.getByText('任务已离开待处理列表')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /ticket:12/ })).toHaveAttribute('aria-pressed', 'true')
})
