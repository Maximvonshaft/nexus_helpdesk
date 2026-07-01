import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const WEBCHAT_TICKET_ID = 9001

function webchatHandoff() {
  return {
    id: 77,
    webchat_conversation_id: 5001,
    conversation_id: 'conv-9001',
    ticket_id: WEBCHAT_TICKET_ID,
    ticket_no: 'NX-9001',
    title: 'Tracking number missing',
    status: 'requested',
    source: 'webchat',
    trigger_type: 'visitor_requested_human',
    reason_code: 'tracking_missing_number',
    reason_text: 'Customer needs help finding a shipment.',
    recommended_agent_action: 'Ask for tracking number and verify the order.',
    requested_at: '2026-07-02T09:00:00Z',
    visitor_name: 'Demo Visitor',
    visitor_email: 'demo@example.test',
    origin: 'webchat_demo',
    last_message: {
      id: 7001,
      direction: 'visitor',
      body_text: 'I cannot find my tracking number.',
      message_type: 'text',
      author_label: 'Demo Visitor',
      created_at: '2026-07-02T09:00:00Z',
    },
    can_accept: true,
    can_decline: true,
    can_force_takeover: true,
    unread_count: 1,
    marked_unread: true,
    ai_pending: false,
    ai_status: 'completed',
    ai_suspended: false,
    handoff_status: 'requested',
    last_event_id: 101,
    last_read_event_id: 100,
  }
}

function webchatConversation(status = 'open') {
  return {
    conversation_id: 'conv-9001',
    ticket_id: WEBCHAT_TICKET_ID,
    ticket_no: 'NX-9001',
    title: 'Tracking number missing',
    status,
    visitor_name: 'Demo Visitor',
    visitor_email: 'demo@example.test',
    origin: 'webchat_demo',
    page_url: '/webchat-demo',
    updated_at: '2026-07-02T09:00:00Z',
    last_message_type: 'text',
    needs_human: status !== 'closed',
    current_handoff_request_id: 77,
    handoff_status: 'requested',
    unread_count: status === 'closed' ? 0 : 1,
    marked_unread: status !== 'closed',
    ai_pending: false,
    ai_status: 'completed',
    ai_suspended: false,
    last_event_id: 101,
    last_read_event_id: 100,
  }
}

function webchatThread() {
  return {
    conversation_id: 'conv-9001',
    ticket_id: WEBCHAT_TICKET_ID,
    ticket_no: 'NX-9001',
    origin: 'webchat_demo',
    page_url: '/webchat-demo',
    status: 'open',
    conversation_state: 'waiting_for_agent',
    required_action: 'ask_tracking_number',
    handoff: webchatHandoff(),
    visitor: {
      name: 'Demo Visitor',
      email: 'demo@example.test',
      phone: null,
      ref: 'demo-webchat',
    },
    messages: [
      {
        id: 7001,
        direction: 'visitor',
        body: 'I cannot find my tracking number.',
        body_text: 'I cannot find my tracking number.',
        message_type: 'text',
        author_label: 'Demo Visitor',
        created_at: '2026-07-02T09:00:00Z',
      },
    ],
    actions: [],
    ai_turns: [
      {
        id: 8001,
        status: 'completed',
        reply_source: 'private_ai_runtime',
        fallback_reason: null,
        bridge_elapsed_ms: 420,
      },
    ],
    events: [],
    last_event_id: 101,
    last_read_event_id: 100,
    unread_count: 1,
    marked_unread: true,
    ai_pending: false,
    ai_status: 'completed',
    ai_suspended: false,
    handoff_status: 'requested',
  }
}

function authUser(kind: 'agent' | 'admin') {
  if (kind === 'admin') {
    return {
      id: 1,
      username: 'admin',
      display_name: 'Admin User',
      role: 'admin',
      capabilities: ['runtime.manage', 'channel_account.manage', 'ai_config.read', 'ai_config.manage', 'user.manage'],
    }
  }
  return {
    id: 2,
    username: 'agent',
    display_name: 'Agent User',
    role: 'agent',
    capabilities: [],
  }
}

async function fulfillApi(route: Route, kind: 'agent' | 'admin') {
  const url = new URL(route.request().url())
  const path = url.pathname

  const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body) })

  if (path === '/api/auth/me') return json(authUser(kind))
  if (path === '/api/lookups/bulletins') return json([])
  if (path === '/api/lookups/markets') return json([{ id: 11, code: 'CH', name: 'Switzerland', country_code: 'CH', is_active: true }])
  if (path === '/api/lite/cases') return json({ items: [], next_cursor: null, has_more: false })
  if (path === '/api/admin/queues/summary') return json({ pending_outbound: 0, dead_outbound: 0, pending_jobs: 0, dead_jobs: 0, external_channel_links: 0 })
  if (path === '/api/admin/external_channel/runtime-health') {
    return json({
      stale_link_count: 0,
      pending_sync_jobs: 0,
      dead_sync_jobs: 0,
      pending_attachment_jobs: 0,
      dead_attachment_jobs: 0,
      warnings: [],
    })
  }
  if (path === '/api/admin/production-readiness') {
    return json({
      app_env: 'development',
      database_url_scheme: 'sqlite',
      is_postgres: false,
      storage_backend: 'local',
      external_channel_transport: 'disabled',
      metrics_enabled: false,
      external_channel_sync_enabled: false,
      outbound_email_production_pilot_enabled: false,
      outbound_email_active_accounts: 1,
      outbound_email_successful_test_send_accounts: 1,
      outbound_email_test_send_max_age_hours: 24,
      warnings: [],
    })
  }
  if (path === '/api/admin/signoff-checklist') return json({ status: 'not_ready', checks: {}, warnings: [] })
  if (path === '/api/admin/channel-accounts') return json([])
  if (path === '/api/outbound/channels/capabilities') {
    return json({
      channels: [
        {
          channel: 'web_chat',
          label: 'WebChat',
          dispatch_type: 'webchat_local',
          status: 'ready',
          customer_sendable: true,
          enabled: true,
          configured: true,
          account_required: false,
          target_required: false,
          supports_send: true,
          supports_inbound_sync: true,
          supports_delivery_receipt: true,
          supports_attachments: false,
          external_send: false,
          missing: [],
          operator_note: null,
        },
      ],
    })
  }
  if (path === '/api/webchat/admin/conversations') return json([webchatConversation(), webchatConversation('closed')])
  if (path === '/api/webchat/admin/handoff/queue') {
    return json({
      items: [webchatHandoff()],
      view: url.searchParams.get('view') || 'requested',
      permissions: {
        can_accept: true,
        can_decline: true,
        can_force_takeover: true,
        can_release: true,
        can_resume_ai: true,
      },
    })
  }
  if (path === `/api/webchat/admin/tickets/${WEBCHAT_TICKET_ID}/thread`) return json(webchatThread())
  if (path === `/api/webchat/admin/tickets/${WEBCHAT_TICKET_ID}/events`) return json({ events: [], last_event_id: 101 })
  if (path === `/api/webchat/admin/tickets/${WEBCHAT_TICKET_ID}/voice/sessions`) return json({ items: [] })
  if (path === `/api/tickets/${WEBCHAT_TICKET_ID}/summary`) {
    return json({
      id: WEBCHAT_TICKET_ID,
      title: 'Tracking number missing',
      status: 'open',
      priority: 'normal',
      market_code: 'CH',
      customer_name: 'Demo Visitor',
      customer_request: 'Needs tracking help',
      issue_summary: 'Tracking number missing',
      last_customer_message: 'I cannot find my tracking number.',
      preferred_reply_channel: 'web_chat',
      preferred_reply_contact: 'demo@example.test',
      tracking_number: null,
      assignee_name: 'Agent User',
      team_name: 'Support',
      updated_at: '2026-07-02T09:00:00Z',
      evidence_summary: {
        loaded: true,
        preview_limit: 3,
        attachments_count: 0,
        external_channel_transcript_count: 1,
        external_channel_attachment_references_count: 0,
        active_market_bulletins_count: 0,
      },
      customer: {
        name: 'Demo Visitor',
        phone: null,
        email: 'demo@example.test',
      },
    })
  }
  if (path === '/api/admin/outbound-email/accounts') {
    return json([
      {
        id: 7,
        display_name: 'Pilot SMTP',
        host: 'smtp.example.test',
        port: 587,
        username: 'support@example.test',
        from_address: 'support@example.test',
        reply_to: 'replies@example.test',
        security_mode: 'starttls',
        market_id: null,
        is_active: true,
        priority: 10,
        health_status: 'ok',
        last_test_status: 'success',
        last_test_error: null,
        last_test_at: '2026-05-27T12:00:00Z',
        password_configured: true,
        password_mask: '********',
        created_at: '2026-05-27T11:00:00Z',
        updated_at: '2026-05-27T12:00:00Z',
      },
    ])
  }

  return route.fulfill({ status: 404, contentType: 'application/json; charset=utf-8', body: JSON.stringify({ detail: `Unhandled mock for ${path}` }) })
}

async function mockAuthenticatedConsole(page: Page, kind: 'agent' | 'admin') {
  await page.addInitScript(([storageKey, token]) => {
    window.sessionStorage.setItem(storageKey, token)
  }, [TOKEN_KEY, `${kind}-token`])
  await page.route('**/api/**', (route) => fulfillApi(route, kind))
}

test('login page renders', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '客服工作台' })).toBeVisible()
  await expect(page.getByLabel('账号')).toBeVisible()
  await expect(page.getByRole('button', { name: '登录' })).toBeVisible()
})

test('unauthenticated protected route redirects back to login', async ({ page }) => {
  await page.goto('/users')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByText('登录状态只保存在当前浏览器会话中。')).toBeVisible()
})

test('agent navigation hides management entry points', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'agent')
  await page.goto('/')

  await expect(page.getByTestId('operator-primary-navigation')).toBeVisible()
  await expect(page.getByRole('link', { name: /处理工单/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /控制面/ })).toHaveCount(0)
  await expect(page.getByRole('link', { name: /账号权限/ })).toHaveCount(0)
  await expect(page.getByRole('link', { name: /发送线路/ })).toHaveCount(0)
})

test('admin-capable navigation shows management entry points', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'admin')
  await page.goto('/')

  await expect(page.getByRole('link', { name: /控制面/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /账号权限/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /发送线路/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /运行恢复/ })).toBeVisible()
})

test('admin can open the lightweight WebChat agent console', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'admin')
  await page.goto('/webchat')

  await expect(page.locator('.page-title', { hasText: '客户会话台' })).toBeVisible()
  await expect(page.getByTestId('agent-console-strip')).toContainText('待接入')
  await expect(page.getByTestId('agent-console-strip')).toContainText('Demo Visitor · NX-9001')
  await expect(page.getByLabel('搜索客户、工单号或消息')).toBeVisible()
  await expect(page.getByRole('option', { name: /Demo Visitor/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Demo Visitor' })).toBeVisible()
})

test('admin can open outbound email configuration page', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'admin')
  await page.goto('/outbound-email')

  await expect(page.getByRole('heading', { name: 'SMTP 账号配置' })).toBeVisible()
  await expect(page.getByText('Pilot SMTP')).toBeVisible()
  await expect(page.getByText('测试发送会发出真实邮件')).toBeVisible()
  await expect(page.getByRole('button', { name: '发送测试邮件' })).toBeVisible()
  await expect(page.getByText('密码：********')).toBeVisible()
})
