import { expect, test, type Page, type Route, type TestInfo } from '@playwright/test'
import {
  json,
  mockResponsiveConsole,
  responsiveUser,
} from './fixtures/responsiveConsole'

function queueItem(index = 42) {
  return {
    queue_id: `ticket:${index}`,
    case_key: `case-${index}`,
    display_label: `T-${index}`,
    display_summary: `Customer delivery inquiry ${index}`,
    source_type: 'ticket',
    source_id: index,
    ticket_id: index,
    conversation_id: null,
    country_code: 'CH',
    channel_key: 'webchat',
    state: 'active',
    source_status: 'in_progress',
    reopened: false,
    priority: index % 17 === 0 ? 'urgent' : 'medium',
    owner: { kind: 'unassigned', user_id: null, team_id: null },
    sla: { state: 'healthy', due_at: '2026-07-26T18:00:00Z', seconds_remaining: 3600 },
    retry: { state: 'not_applicable', attempt_count: 0, max_attempts: 0, next_retry_at: null, error_category: null },
    created_at: '2026-07-25T08:00:00Z',
    updated_at: '2026-07-25T09:00:00Z',
    source_links: { ticket: `/api/tickets/${index}`, conversation: null, handoff: null, dispatch: null },
  }
}

function queueResponse(items: ReturnType<typeof queueItem>[], nextCursor: string | null = null) {
  return {
    items,
    next_cursor: nextCursor,
    scope: { tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' },
    filters: { state: 'active', source_type: null, owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
  }
}

function closureReceipt(options?: { ready?: boolean; repair?: boolean; closed?: boolean }) {
  const ready = Boolean(options?.ready)
  const repair = Boolean(options?.repair)
  return {
    schema: 'nexus.ticket-closure-receipt.v1',
    ticket_id: 42,
    ticket_status: options?.closed ? 'closed' : 'in_progress',
    ticket_revision: '2026-07-25T09:00:00Z',
    scenario_key: 'parcel.delay',
    scenario_catalog_version: 'v1',
    scenario_catalog_sha256: 'a'.repeat(64),
    generated_at: '2026-07-25T09:01:00Z',
    readiness: {
      scenario_key: 'parcel.delay',
      closure_ready: ready,
      missing_fact_classes: ready ? [] : ['tracking.current_status'],
      missing_customer_inputs: [],
      missing_action_classes: [],
      missing_outcome_levels: repair ? ['business_result_confirmed'] : ready ? [] : ['operational_completed'],
      notification_satisfied: ready,
      blocked_reasons: repair ? ['repair_required'] : ready ? [] : ['fact:tracking.current_status'],
    },
    evidence: {
      ticket_event_ids: [1],
      background_job_ids: [],
      outbound_message_ids: ready ? [2] : [],
      latest_material_at: '2026-07-25T09:00:00Z',
      observation_elapsed: ready,
      contains_payloads: false,
    },
    receipt_sha256: 'b'.repeat(64),
  }
}

async function mockTicketWorkspace(page: Page, receipt = closureReceipt()) {
  await mockResponsiveConsole(page)
  await page.route('**/api/admin/operator-queue/unified?*', (route) => json(route, queueResponse([queueItem()])))
  await page.route('**/api/tickets/42/closure-readiness', (route) => json(route, receipt))
  await page.route('**/api/tickets/42', (route) => json(route, {
    id: 42,
    ticket_no: 'T-42',
    title: 'Customer supplied title must remain verbatim: helpdesk sync MCP CLI',
    status: 'in_progress',
    priority: 'high',
  }))
}

async function capture(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' })
}

test('normal and empty canonical surfaces produce deterministic visual evidence', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockResponsiveConsole(page)
  await page.goto('/workspace')
  await expect(page.getByText('暂无待处理任务')).toBeVisible()
  await capture(page, testInfo, 'workspace-empty-1440')

  await page.goto('/administration')
  await expect(page.getByRole('heading', { level: 1, name: '系统管理' })).toBeVisible()
  await capture(page, testInfo, 'administration-normal-1440')
})

test('visible primary controls meet the 44px target contract', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockResponsiveConsole(page)
  await page.goto('/administration')
  const targets = page.locator('button:visible, a[href]:visible, [role="tab"]:visible, [role="combobox"]:visible')
  const count = await targets.count()
  expect(count).toBeGreaterThan(5)
  for (let index = 0; index < count; index += 1) {
    const box = await targets.nth(index).boundingBox()
    expect(box?.height ?? 0, `target ${index} is below 44px`).toBeGreaterThanOrEqual(44)
  }
})

test('long operator identity and 200 percent text remain inside the viewport', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await mockResponsiveConsole(page)
  await page.route('**/api/auth/me', (route) => json(route, {
    ...responsiveUser,
    display_name: 'Extremely Long Multi-Country Operations Administrator Name 德语 Français Italiano',
  }))
  await page.goto('/workspace')
  await page.addStyleTag({ content: 'html { font-size: 200% !important; }' })
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await capture(page, testInfo, 'workspace-zoom-200-long-content-1366')
})

test('slow queue loading is explicit and visually evidenced', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockResponsiveConsole(page)
  await page.route('**/api/admin/operator-queue/unified?*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_000))
    await json(route, queueResponse([]))
  })
  await page.goto('/workspace')
  await expect(page.getByText('正在读取任务…')).toBeVisible()
  await capture(page, testInfo, 'workspace-loading-375')
  await expect(page.getByText('暂无待处理任务')).toBeVisible()
})

test('failed background refresh preserves the last confirmed queue', async ({ page }, testInfo) => {
  await page.clock.install()
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockResponsiveConsole(page)
  let calls = 0
  await page.route('**/api/admin/operator-queue/unified?*', (route) => {
    calls += 1
    if (calls === 1) return json(route, queueResponse([queueItem()]))
    return json(route, { detail: 'provider unavailable' }, 503)
  })
  await page.route('**/api/tickets/42/closure-readiness', (route) => json(route, closureReceipt()))
  await page.route('**/api/tickets/42', (route) => json(route, { id: 42, title: 'Existing safe information', status: 'in_progress', priority: 'medium' }))
  await page.goto('/workspace')
  const queueRow = page.getByRole('button', { name: /T-42/ })
  await expect(queueRow).toBeVisible()
  await page.clock.fastForward(16_000)
  await expect(page.getByText(/待处理列表刷新失败，当前显示上次服务器确认的信息/)).toBeVisible()
  await expect(queueRow).toBeVisible()
  await capture(page, testInfo, 'workspace-degraded-last-safe-1440')
})

test('repair-required state is server-derived and visually persistent', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockTicketWorkspace(page, closureReceipt({ repair: true }))
  await page.goto('/workspace')
  await expect(page.getByLabel('处理进度').getByText('存在失败结果，需要修复')).toBeVisible()
  await expect(page.getByLabel('安全关闭').getByText('需要修复失败结果')).toBeVisible()
  await expect(page.getByText('已安全关闭')).toHaveCount(0)
  await expect(page.getByText('Customer supplied title must remain verbatim: helpdesk sync MCP CLI')).toBeVisible()
  await capture(page, testInfo, 'workspace-repair-required-1440')
})

test('stale close conflict requires review and cannot display false success', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await mockTicketWorkspace(page, closureReceipt({ ready: true }))
  await page.route('**/api/tickets/42/status', (route) => json(route, { detail: 'ticket_revision_conflict' }, 409))
  await page.goto('/workspace')
  await page.getByRole('button', { name: '核对并关闭' }).click()
  const dialog = page.getByRole('dialog', { name: '确认安全关闭工单？' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '确认安全关闭' }).click()
  await expect(page.getByText('关闭条件已发生变化')).toBeVisible()
  await expect(page.getByText('已安全关闭')).toHaveCount(0)
  await capture(page, testInfo, 'workspace-stale-conflict-1440')
})

test('five hundred queue rows remain operable through bounded cursor pages', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockResponsiveConsole(page)
  await page.route('**/api/admin/operator-queue/unified?*', (route: Route) => {
    const url = new URL(route.request().url())
    const pageIndex = Number(url.searchParams.get('cursor') || 0)
    const start = pageIndex * 50
    const items = Array.from({ length: 50 }, (_, offset) => {
      const item = queueItem(start + offset + 1)
      return { ...item, ticket_id: null, source_links: { ticket: null, conversation: null, handoff: null, dispatch: null } }
    })
    return json(route, queueResponse(items, pageIndex < 9 ? String(pageIndex + 1) : null))
  })
  await page.goto('/workspace')
  for (let pageIndex = 1; pageIndex < 10; pageIndex += 1) {
    await page.getByRole('button', { name: '加载更多任务' }).click()
  }
  const rows = page.locator('#workspace-queue .MuiListItemButton-root')
  await expect(rows).toHaveCount(500)
  await rows.nth(499).click()
  await expect(rows.nth(499)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('heading', { level: 1, name: 'T-500' })).toBeVisible()
})
