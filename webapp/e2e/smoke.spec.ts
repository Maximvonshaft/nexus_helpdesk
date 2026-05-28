import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'

function authUser(kind: 'agent' | 'admin') {
  if (kind === 'admin') {
    return {
      id: 1,
      username: 'admin',
      display_name: 'Admin User',
      role: 'admin',
      capabilities: ['ticket.read', 'outbound.send', 'runtime.manage', 'channel_account.manage', 'ai_config.read', 'ai_config.manage', 'user.manage', 'webcall.voice.read', 'webcall.voice.queue.view', 'webcall.voice.accept', 'webcall.voice.reject', 'webcall.voice.end'],
    }
  }
  return {
    id: 2,
    username: 'agent',
    display_name: 'Agent User',
    role: 'agent',
    capabilities: ['ticket.read', 'outbound.send', 'webcall.voice.read', 'webcall.voice.queue.view'],
  }
}

async function fulfillApi(route: Route, kind: 'agent' | 'admin') {
  const url = new URL(route.request().url())
  const path = url.pathname

  const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body) })

  if (path === '/api/auth/me') return json(authUser(kind))
  if (path === '/api/lookups/bulletins') return json([])
  if (path === '/api/lookups/markets') return json([{ id: 11, code: 'CH', name: 'Switzerland', country_code: 'CH', is_active: true }])
  if (path === '/api/lite/cases') {
    return json({
      items: [
        {
          id: 101,
          ticket_no: 'TK-101',
          title: 'Customer email about delayed parcel',
          status: 'in_progress',
          priority: 'high',
          source_channel: 'email',
          category: 'delivery',
          sub_category: 'delay',
          tracking_number: 'NX-EMAIL-101',
          customer_name: 'Maria Email',
          assignee_name: 'Agent User',
          team_name: 'Support',
          market_code: 'CH',
          country_code: 'CH',
          conversation_state: 'customer_waiting',
          updated_at: '2026-05-28T08:30:00Z',
          resolution_due_at: '2026-05-28T12:00:00Z',
          overdue: false,
        },
      ],
      next_cursor: null,
      has_more: false,
    })
  }
  if (path === '/api/tickets/101/summary') {
    return json({
      id: 101,
      title: 'Customer email about delayed parcel',
      status: 'in_progress',
      priority: 'high',
      market_code: 'CH',
      country_code: 'CH',
      conversation_state: 'customer_waiting',
      customer_name: 'Maria Email',
      customer_request: 'Please confirm the latest delivery date.',
      issue_summary: 'Shipment delay email requires agent follow-up.',
      last_customer_message: 'Can you tell me when my parcel will arrive?',
      preferred_reply_channel: 'email',
      preferred_reply_contact: 'maria@example.test',
      tracking_number: 'NX-EMAIL-101',
      assignee_name: 'Agent User',
      team_name: 'Support',
      updated_at: '2026-05-28T08:30:00Z',
      ai_summary: 'Customer is asking for delivery ETA.',
      ai_classification: 'delivery_delay',
      required_action: 'Check tracking status and reply with clear ETA.',
      missing_fields: '',
      customer_update: 'We are checking the latest delivery status and will confirm the ETA.',
      resolution_summary: '',
      attachments: [],
      openclaw_attachment_references: [],
      active_market_bulletins: [],
      customer: { name: 'Maria Email', email: 'maria@example.test', phone: '+410000000' },
      openclaw_conversation: null,
    })
  }
  if (path === '/api/tickets/101/timeline') {
    return json({
      items: [
        { id: 1, source_type: 'comment', body: 'Can you tell me when my parcel will arrive?', created_at: '2026-05-28T08:20:00Z' },
        { id: 2, source_type: 'ticket_event', event_type: 'assigned', created_at: '2026-05-28T08:25:00Z' },
      ],
      next_cursor: null,
      has_more: false,
    })
  }
  if (path === '/api/tickets/101/outbound/channels/capabilities') {
    return json({
      channels: [
        {
          channel: 'email',
          label: 'Email',
          dispatch_type: 'external',
          status: 'ready',
          customer_sendable: true,
          enabled: true,
          configured: true,
          account_required: true,
          target_required: true,
          supports_send: true,
          supports_inbound_sync: true,
          supports_delivery_receipt: true,
          supports_attachments: false,
          external_send: true,
          target_validation: 'email',
          missing: [],
          operator_note: 'Uses market SMTP account with global fallback.',
        },
      ],
    })
  }
  if (path === '/api/webchat/admin/conversations') {
    return json([
      {
        conversation_id: 'conv-101',
        ticket_id: 101,
        ticket_no: 'TK-101',
        title: 'Customer email about delayed parcel',
        visitor_name: 'Maria Email',
        visitor_email: 'maria@example.test',
        visitor_phone: '+410000000',
        status: 'open',
        origin: 'website',
        page_url: 'https://example.test/help',
        needs_human: true,
        updated_at: '2026-05-28T08:30:00Z',
      },
    ])
  }
  if (path === '/api/webchat/admin/voice/sessions') {
    return json({
      items: [
        {
          ticket_id: 101,
          ticket_no: 'TK-101',
          ticket_title: 'Customer email about delayed parcel',
          conversation_id: 'conv-101',
          visitor_label: 'Maria Email',
          origin: 'website',
          page_url: 'https://example.test/help',
          voice_session_id: 'voice-101',
          status: 'ringing',
          provider: 'livekit',
          room_name: 'room-101',
          provider_room_name: 'room-101',
          ringing_at: '2026-05-28T08:31:00Z',
          recording_status: 'off',
          transcript_status: 'pending',
          summary_status: 'pending',
        },
      ],
    })
  }
  if (path === '/api/webchat/admin/tickets/101/voice/sessions') {
    return json({
      items: [
        {
          voice_session_id: 'voice-101',
          status: 'ringing',
          provider: 'livekit',
          room_name: 'room-101',
          provider_room_name: 'room-101',
          ringing_at: '2026-05-28T08:31:00Z',
          recording_status: 'off',
          transcript_status: 'pending',
          summary_status: 'pending',
        },
      ],
    })
  }
  if (path === '/api/webchat/voice/runtime-config') {
    return json({ enabled: true, provider: 'livekit', livekit_url: 'wss://livekit.example.test', recording_enabled: false, transcription_enabled: true })
  }
  if (path === '/api/admin/queues/summary') return json({ pending_outbound: 0, dead_outbound: 0, pending_jobs: 0, dead_jobs: 0, openclaw_links: 0 })
  if (path === '/api/admin/openclaw/runtime-health') {
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
      openclaw_transport: 'mcp',
      metrics_enabled: false,
      openclaw_sync_enabled: true,
      outbound_email_production_pilot_enabled: false,
      outbound_email_active_accounts: 1,
      outbound_email_successful_test_send_accounts: 1,
      outbound_email_test_send_max_age_hours: 24,
      warnings: [],
    })
  }
  if (path === '/api/admin/signoff-checklist') return json({ status: 'not_ready', checks: {}, warnings: [] })
  if (path === '/api/admin/channel-accounts') return json([])
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
  await expect(page.getByRole('link', { name: /今日工作台/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /WebChat/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /WebCall/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /^Email/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /工单处理/ })).toBeVisible()
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
  await expect(page.getByRole('link', { name: /SMTP 账号/ })).toBeVisible()
  await expect(page.getByRole('link', { name: /运行恢复/ })).toBeVisible()
})

test('agent can open webcall workbench', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'agent')
  await page.goto('/webcall')

  await expect(page.getByRole('heading', { name: 'WebCall 语音接听台' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '来电队列' })).toBeVisible()
  await expect(page.getByText('Agent WebCall')).toBeVisible()
  await expect(page.getByTestId('webcall-session-queue').getByText('voice-101', { exact: true })).toBeVisible()
})

test('agent can open email workbench', async ({ page }) => {
  await mockAuthenticatedConsole(page, 'agent')
  await page.goto('/email')

  await expect(page.getByRole('heading', { name: 'Email 客服处理台' })).toBeVisible()
  await expect(page.getByText('Email Queue')).toBeVisible()
  await expect(page.getByRole('button', { name: /Customer email about delayed parcel/ })).toBeVisible()
  await expect(page.getByText('Reply Composer / Guardrails')).toBeVisible()
  await expect(page.getByRole('button', { name: '发送 Email' })).toBeVisible()
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
