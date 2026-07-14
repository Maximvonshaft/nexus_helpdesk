import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const SCOPE_KEY = 'nexus-operator-workspace-scope'

const user = {
  id: 9,
  username: 'operator',
  display_name: '客服一号',
  role: 'agent',
  capabilities: ['ticket.read', 'operator_queue.read', 'outbound.send', 'webchat.handoff.accept', 'webchat.handoff.resume_ai', 'tool:speedaf.work_order.create:write', 'ai_config.read'],
}

function queueItem() {
  return {
    queue_id: 'handoff:21', case_key: 'ticket:11', source_type: 'handoff', source_id: 21, ticket_id: 11, conversation_id: 1,
    country_code: 'CH', channel_key: 'webchat', state: 'active', source_status: 'requested', reopened: false, priority: 'high',
    owner: { kind: 'unassigned', user_id: null, team_id: null },
    sla: { state: 'at_risk', due_at: '2026-07-14T12:00:00Z', seconds_remaining: 900 },
    retry: { state: 'not_applicable', attempt_count: 0, max_attempts: 0, next_retry_at: null, error_category: null },
    created_at: '2026-07-14T10:00:00Z', updated_at: '2026-07-14T10:10:00Z',
    source_links: { ticket: '/api/tickets/11', conversation: '/api/webchat/admin/tickets/11/thread', handoff: null, dispatch: null },
  }
}

function thread() {
  return {
    conversation_id: 'conv-1', ticket_id: 11, ticket_no: 'T-11', status: 'in_progress', conversation_state: 'human_review_required', required_action: '核实运单后回复客户',
    visitor: { name: '张女士', email: null, phone: '+41790000000', ref: 'customer-1' },
    messages: [
      { id: 1, direction: 'visitor', body: '我的包裹为什么还没到？', body_text: '我的包裹为什么还没到？', created_at: '2026-07-14T10:00:00Z' },
      { id: 2, direction: 'ai', body: '已收到您的问题。', body_text: '已收到您的问题。', delivery_status: 'delivered', created_at: '2026-07-14T10:01:00Z' },
    ],
    handoff: { id: 21, ticket_id: 11, status: 'requested', reason_text: '客户要求人工处理', recommended_agent_action: '核实运单', can_accept: true, can_force_takeover: true, can_release: false, can_resume_ai: true, can_reply: false },
    support_memory: {
      source: 'derived_support_memory_ledger', ticket: { id: 11, ticket_no: 'T-11', status: 'in_progress', country_code: 'CH' }, conversation: { id: 'conv-1', status: 'open', channel_key: 'webchat' },
      customer_request: '查询延迟包裹', required_action: '核实运单后回复客户', missing_fields: ['运单号'], tracking: { present: false }, ai_state: {}, evidence_summary: {},
      evidence_timeline: [{ kind: 'ai_turn', label: 'automatic response', status: 'completed', summary: {}, created_at: '2026-07-14T10:01:00Z', source_id: 'turn:1' }],
      next_actions: [{ key: 'collect_tracking', label: '向客户确认运单号', tone: 'warning' }],
    },
    unread_count: 1,
  }
}

async function mockWorkspace(page: Page) {
  await page.addInitScript(([tokenKey, scopeKey]) => {
    sessionStorage.setItem(tokenKey, 'operator-token')
    sessionStorage.setItem(scopeKey, JSON.stringify({ tenantKey: 'default', countryCode: 'CH', channelKey: 'webchat' }))
  }, [TOKEN_KEY, SCOPE_KEY])
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body) })
    if (url.pathname === '/api/auth/me') return json(user)
    if (url.pathname === '/api/admin/operator-queue/unified') {
      expect(route.request().headers()['x-nexus-tenant']).toBe('default')
      return json({ items: [queueItem()], next_cursor: null, scope: { tenant_hash: 'hash', country_code: 'CH', channel_key: 'webchat' }, filters: {} })
    }
    if (url.pathname === '/api/webchat/admin/tickets/11/thread') return json(thread())
    if (url.pathname.endsWith('/accept')) return json({ ...thread().handoff, status: 'accepted', can_reply: true })
    return route.fulfill({ status: 404, contentType: 'application/json; charset=utf-8', body: JSON.stringify({ detail: url.pathname }) })
  })
}

test('workspace presents the case from the customer-service perspective without visible internal automation terms', async ({ page }) => {
  await mockWorkspace(page)
  await page.goto('/workspace')
  await expect(page.getByRole('heading', { level: 1, name: '客服工作台' })).toBeVisible()
  await expect(page.getByText('张女士')).toBeVisible()
  await expect(page.getByText('我的包裹为什么还没到？').first()).toBeVisible()
  await expect(page.getByRole('heading', { name: '事实与待确认信息' })).toBeVisible()
  await expect(page.getByText('历史处理建议')).toBeVisible()
  await expect(page.getByText(/\b(?:AI|Runtime|Provider|RAG|Prompt|Model|Agent)\b/i)).toHaveCount(0)
})

test('375px keeps all four service tasks reachable and touch sized', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockWorkspace(page)
  await page.goto('/workspace')
  for (const label of ['待办', '案例', '沟通', '处理']) {
    const button = page.getByRole('button', { name: label, exact: true })
    await expect(button).toBeVisible()
    expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  await page.getByRole('button', { name: '沟通', exact: true }).click()
  await expect(page.getByRole('heading', { name: '客户沟通' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('workspace-conversation')
  await page.getByRole('button', { name: '处理', exact: true }).click()
  await expect(page.getByRole('heading', { name: '处理动作' })).toBeVisible()
})

test('unsent reply is protected before route navigation', async ({ page }) => {
  await mockWorkspace(page)
  await page.goto('/workspace')
  await page.getByRole('textbox', { name: '回复客户' }).fill('我正在核实，请稍候。')
  await page.getByRole('link', { name: /知识与规则/ }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('放弃未发送的回复？')).toBeVisible()
  await page.getByRole('button', { name: '取消' }).click()
  await expect(page).toHaveURL(/\/workspace/)
})

test('historical webchat route redirects to the single workspace', async ({ page }) => {
  await mockWorkspace(page)
  await page.goto('/webchat')
  await expect(page).toHaveURL(/\/workspace(?:\?.*)?$/)
  await expect(page.getByRole('heading', { level: 1, name: '客服工作台' })).toBeVisible()
})
