import { expect, test, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const LOCALE_KEY = 'nexus-operator-ui-locale'
const SCOPE_KEY = 'nexus-operator-workspace-scope'
const CUSTOMER_MESSAGE = '我的包裹为什么还没有到？'

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

test('English operator chrome preserves Chinese customer evidence verbatim', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.addInitScript(([tokenKey, localeKey, scopeKey]) => {
    sessionStorage.setItem(tokenKey, 'i18n-evidence-token')
    localStorage.setItem(localeKey, 'en')
    sessionStorage.setItem(scopeKey, JSON.stringify({
      tenantKey: 'default',
      countryCode: 'CH',
      channelKey: 'webchat',
      queueKey: 'legacy',
    }))
  }, [TOKEN_KEY, LOCALE_KEY, SCOPE_KEY])

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, {
        id: 9,
        username: 'operator',
        display_name: 'Operations Agent',
        role: 'agent',
        ui_locale: 'en',
        ui_locale_configured: true,
        capabilities: [
          'ticket.read',
          'operator_queue.read',
          'outbound.send',
          'webchat.handoff.accept',
        ],
      })
    }
    if (url.pathname === '/api/operator/agent-state' || url.pathname === '/api/operator/agent-state/heartbeat') {
      return json(route, {
        status: 'online',
        max_concurrent_conversations: 3,
        active_conversations: 0,
        available_capacity: 3,
        voice_enabled: false,
        max_concurrent_voice_calls: 1,
        active_voice_calls: 0,
        reserved_voice_offers: 0,
        available_voice_capacity: 0,
        voice_wrap_up_seconds: 30,
        last_heartbeat_at: '2026-08-01T00:00:00Z',
        heartbeat_ttl_seconds: 90,
      })
    }
    if (url.pathname === '/api/webchat/admin/voice/sessions') return json(route, { items: [] })
    if (url.pathname === '/api/observability/frontend-metrics') return route.fulfill({ status: 204 })
    if (url.pathname === '/api/admin/operator-queue/my-scopes') {
      return json(route, {
        items: [{
          tenant_key: 'default',
          tenant_hash: '123456789abc',
          country_code: 'CH',
          channel_key: 'webchat',
          queue_key: 'legacy',
        }],
        requires_explicit_admin_scope: false,
      })
    }
    if (url.pathname === '/api/admin/operator-queue/unified') {
      return json(route, {
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
          sla: { state: 'at_risk', due_at: '2026-08-01T04:00:00Z', seconds_remaining: 900 },
          retry: { state: 'not_applicable', attempt_count: 0, max_attempts: 0, next_retry_at: null, error_category: null },
          created_at: '2026-08-01T02:00:00Z',
          updated_at: '2026-08-01T02:10:00Z',
          source_links: {
            ticket: '/api/tickets/11',
            conversation: '/api/webchat/admin/tickets/11/thread',
            handoff: '/api/webchat/admin/handoff/queue',
            dispatch: null,
          },
        }],
        next_cursor: null,
        scope: {
          tenant_hash: '123456789abc',
          country_code: 'CH',
          channel_key: 'webchat',
          queue_key: 'legacy',
        },
        filters: { state: 'active', source_type: null, owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
      })
    }
    if (url.pathname === '/api/webchat/admin/tickets/11/thread') {
      return json(route, {
        conversation_id: 'conv-1',
        ticket_id: 11,
        ticket_no: 'T-11',
        status: 'in_progress',
        conversation_state: 'human_review_required',
        required_action: '核实运单后回复客户',
        visitor: { name: 'Customer', email: null, phone: null, ref: 'customer-1' },
        messages: [{
          id: 1,
          direction: 'visitor',
          body: CUSTOMER_MESSAGE,
          body_text: CUSTOMER_MESSAGE,
          delivery_status: 'sent',
          created_at: '2026-08-01T02:00:00Z',
        }],
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
          can_force_takeover: false,
          can_release: false,
          can_resume_ai: true,
          can_reply: false,
        },
        support_memory: {
          source: 'derived_support_memory_ledger',
          ticket: { id: 11, ticket_no: 'T-11', status: 'in_progress', country_code: 'CH' },
          conversation: { id: 'conv-1', status: 'open', channel_key: 'webchat' },
          current_intent: 'tracking_status',
          customer_request: CUSTOMER_MESSAGE,
          required_action: '核实运单后回复客户',
          missing_fields: ['tracking_number'],
          tracking: { present: false },
          ai_state: {},
          evidence_summary: { outbound_messages: 0 },
          evidence_timeline: [],
          next_actions: [],
        },
        unread_count: 1,
        marked_unread: false,
      })
    }
    if (/^\/api\/webchat\/admin\/tickets\/\d+\/events$/.test(url.pathname)) {
      return json(route, { events: [], last_event_id: 0, has_more: false, wait_ms: 0 })
    }
    return json(route, { detail: `Unhandled i18n evidence API ${url.pathname}` }, 404)
  })

  await page.goto('/workspace')

  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('combobox', { name: 'Interface language' }).first()).toBeVisible()
  const message = page.getByText(CUSTOMER_MESSAGE, { exact: true }).first()
  await expect(message).toBeVisible()
  await expect(message).toHaveText(CUSTOMER_MESSAGE)
})
