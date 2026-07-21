import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

const authUser = {
  id: 8,
  username: 'operations-manager',
  display_name: 'Operations Manager',
  role: 'admin',
  capabilities: [
    'operator_queue.read',
    'ticket.read',
    'ticket.assign',
    'bulletin.manage',
    'channel_account.manage',
    'runtime.manage',
    'ai_config.read',
    'ai_config.manage',
    'user.manage',
  ],
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

function agentControlSnapshot() {
  return {
    generated_at: Date.now() / 1000,
    tenant_key: 'default',
    scope: { market_id: null, channel: 'webchat', language: null },
    personas: [],
    persona_total: 0,
    resources: [],
    resolved_playbooks: [],
    tools: [],
    tool_policies: [],
    integrations: [],
    memory_policy: {},
    capabilities: { can_manage: true, playground_model_execution: true },
  }
}

function knowledgeItem(id: number, title: string) {
  return {
    id,
    item_key: `support.customer.${id}`,
    title,
    summary: '',
    status: 'active',
    source_type: 'text',
    knowledge_kind: 'business_fact',
    channel: null,
    audience_scope: 'customer',
    language: null,
    priority: 100,
    fact_question: `${title}怎么处理？`,
    fact_answer: `${title}的事实和处理规则`,
    fact_aliases_json: [],
    fact_status: 'approved',
    answer_mode: 'guided_answer',
    draft_body: `${title}的事实和处理规则`,
    published_body: `${title}的事实和处理规则`,
    published_version: 1,
  }
}

async function seedSession(page: Page) {
  await page.addInitScript(([key, token]) => sessionStorage.setItem(key, token), [TOKEN_KEY, 'operator-token'])
}

async function openKnowledgeTab(page: Page) {
  await page.goto('/knowledge')
  await expect(page).toHaveURL(/\/knowledge$/)
  await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: 'Agent 配置' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { level: 1, name: 'Agent 配置' })).toBeVisible()
  await page.getByRole('tab', { name: '知识', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: '知识与流程' })).toBeVisible()
}

test('Knowledge uses operator language and guards an unsaved draft', async ({ page }) => {
  await seedSession(page)
  const first = knowledgeItem(1, '末派失败')
  const second = knowledgeItem(2, '取消订单')
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, authUser)
    if (url.pathname === '/api/agent-control/snapshot') return json(route, agentControlSnapshot())
    if (url.pathname === '/api/lite/knowledge-studio') {
      return json(route, { kpis: [{ key: 'active', label: '已上线', value: 2, hint: '', tone: 'success' }] })
    }
    if (url.pathname === '/api/knowledge-items' && route.request().method() === 'GET') {
      return json(route, { items: [first, second], total: 2 })
    }
    return json(route, { detail: `Unhandled test API ${url.pathname}` }, 404)
  })

  await openKnowledgeTab(page)
  await expect(page.getByRole('textbox', { name: '标准答案与处理步骤', exact: true })).toBeVisible()
  await expect(page.getByText('AI 应该知道的答案')).toHaveCount(0)
  await expect(page.getByText('让 AI 组织语言')).toHaveCount(0)

  await page.getByRole('textbox', { name: '知识标题', exact: true }).fill('已修改但未保存')
  await page.getByRole('button', { name: /取消订单/ }).click()
  await expect(page.getByRole('dialog', { name: '放弃未保存的修改？' })).toBeVisible()
  await page.getByRole('button', { name: '放弃修改' }).click()
  await expect(page.getByRole('textbox', { name: '知识标题', exact: true })).toHaveValue('取消订单')
})

test('Knowledge retrieval test explains whether the current service can use the result', async ({ page }) => {
  await seedSession(page)
  const first = knowledgeItem(1, '末派失败')
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, authUser)
    if (url.pathname === '/api/agent-control/snapshot') return json(route, agentControlSnapshot())
    if (url.pathname === '/api/lite/knowledge-studio') return json(route, { kpis: [] })
    if (url.pathname === '/api/knowledge-items' && route.request().method() === 'GET') return json(route, { items: [first], total: 1 })
    if (url.pathname === '/api/knowledge-items/retrieve-test') {
      return json(route, {
        grounding_would_apply: true,
        hits: [{
          item_id: 1,
          chunk_index: 0,
          title: '末派失败',
          text: '先确认运单号和联系方式。',
          direct_answer: '先确认运单号和联系方式。',
          score: 0.91,
        }],
      })
    }
    return json(route, { detail: `Unhandled test API ${url.pathname}` }, 404)
  })

  await openKnowledgeTab(page)
  const searchTest = page.getByRole('complementary', { name: '搜索测试和发布状态' })
  await searchTest.getByRole('textbox', { name: '客户问题', exact: true }).fill('包裹派送失败怎么办')
  await searchTest.getByRole('button', { name: '测试搜索' }).click()
  await expect(searchTest.getByText('找到 1 条')).toBeVisible()
  await expect(searchTest.getByText('可用于回复')).toBeVisible()
})

test('Control Tower accepts canonical hrefs and fails closed for retired routes', async ({ page }) => {
  await seedSession(page)
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, authUser)
    if (url.pathname === '/api/lite/control-tower') {
      return json(route, {
        generated_at: '2026-07-14T12:00:00Z',
        role: 'admin',
        user_id: 8,
        capabilities: authUser.capabilities,
        kpis: [
          { key: 'unassigned', label: '未分配', value: 12, hint: '需要分配责任人', tone: 'warning' },
          { key: 'sla', label: 'SLA 风险', value: 3, hint: '即将或已经超时', tone: 'danger' },
        ],
        manager_actions: [
          { key: 'assign-unassigned', label: '调度未分配队列', count: 12, tone: 'warning', next: '进入工作台完成分配', href: '/workspace', capability: 'ticket.assign', enabled: true },
          { key: 'provider-ops', label: '巡检渠道账号', count: 1, tone: 'warning', next: '检查渠道连接', href: '/accounts', capability: 'channel_account.manage', enabled: true },
          { key: 'review-ai-rules', label: '复核配置', count: 2, tone: 'default', next: '复核知识与规则', href: '/ai-control', capability: 'ai_config.manage', enabled: true },
        ],
        team_workload: [{ team_id: 2, team_name: '客服一组', active_tickets: 20, unassigned: 4, sla_risk: 2, overdue: 1 }],
        channel_health: [],
        bulletin_impact: [],
        governance_lanes: [
          { key: 'runtime', area: '运行服务', value: 1, risk: 'warning', next: '检查降级状态', href: '/runtime', capability: 'runtime.manage', enabled: true },
        ],
        template_blocks: [],
        facts: {},
      })
    }
    return json(route, { detail: `Unhandled test API ${url.pathname}` }, 404)
  })

  await page.goto('/control-tower')

  await expect(page).toHaveURL(/\/control-tower$/)
  await expect(page.getByRole('heading', { level: 1, name: '运营监控' })).toBeVisible()
  const kpiRegion = page.getByLabel('关键运营指标')
  await expect(kpiRegion.getByText('未分配', { exact: true }).locator('..')).toContainText('12')
  const links = page.getByRole('link', { name: '去处理' })
  await expect(links.nth(0)).toHaveAttribute('href', '/workspace')
  await expect(links).toHaveCount(1)
  await expect(page.getByText('暂时无法打开')).toHaveCount(2)
  await expect(page.locator('a[href="/accounts"]')).toHaveCount(0)
  await expect(page.locator('a[href="/ai-control"]')).toHaveCount(0)
})
