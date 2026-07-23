import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const SCOPE_KEY = 'nexus-operator-workspace-scope'
const VOICE_CAPABILITIES = [
  'webcall.voice.read',
  'webcall.voice.queue.view',
  'webcall.voice.accept',
  'webcall.voice.reject',
  'webcall.voice.end',
  'webcall.voice.control',
]

function agentState() {
  return {
    user_id: 9,
    status: 'online',
    heartbeat_fresh: true,
    assignable: true,
    voice_enabled: true,
    voice_assignable: true,
    max_concurrent_conversations: 3,
    active_conversations: 0,
    available_capacity: 3,
    max_concurrent_voice_calls: 1,
    active_voice_calls: 0,
    reserved_voice_offers: 1,
    available_voice_capacity: 0,
    voice_wrap_up_seconds: 30,
    last_heartbeat_at: new Date().toISOString(),
    heartbeat_ttl_seconds: 90,
  }
}

async function mockIncomingVoice(page: Page) {
  let offerVisible = true
  let rejectCount = 0
  let acceptCount = 0

  await page.addInitScript(([tokenKey, scopeKey]) => {
    sessionStorage.setItem(tokenKey, 'operator-token')
    sessionStorage.setItem(scopeKey, JSON.stringify({ tenantKey: 'default', countryCode: 'ME', channelKey: 'voice' }))
  }, [TOKEN_KEY, SCOPE_KEY])

  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const json = (body: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(body),
    })

    if (url.pathname === '/api/auth/me') {
      return json({
        id: 9,
        username: 'voice-agent',
        display_name: 'Voice Agent',
        role: 'agent',
        capabilities: ['operator_queue.read', 'webchat.handoff.accept', ...VOICE_CAPABILITIES],
      })
    }
    if (url.pathname === '/api/admin/operator-queue/my-scopes') {
      return json({
        items: [{
          tenant_key: 'default',
          tenant_hash: 'tenant-hash',
          country_code: 'ME',
          channel_key: 'voice',
        }],
        requires_explicit_admin_scope: false,
      })
    }
    if (url.pathname === '/api/admin/operator-queue/unified') {
      return json({
        items: [],
        next_cursor: null,
        scope: { tenant_hash: 'tenant-hash', country_code: 'ME', channel_key: 'voice' },
        filters: { state: 'active', source_type: null, owner: 'any', priority: null, sla: null, retry: null, sort: 'oldest' },
      })
    }
    if (url.pathname === '/api/operator/agent-state') return json(agentState())
    if (url.pathname === '/api/operator/agent-state/heartbeat') return json(agentState())
    if (url.pathname === '/api/webchat/admin/voice/sessions') {
      const items = offerVisible ? [{
        voice_session_id: 'wv_offer_1',
        status: 'ringing',
        provider: 'livekit',
        conversation_id: 'wc_voice_1',
        ticket_id: null,
        ticket_no: null,
        ticket_title: null,
        visitor_label: 'Montenegro Caller',
        origin: 'livekit_sip',
        page_url: null,
        voice_offer: {
          id: 'vo_offer_1',
          expires_at: new Date(Date.now() + 60_000).toISOString(),
        },
      }] : []
      return json({ items })
    }
    if (url.pathname === '/api/webchat/admin/voice/wv_offer_1/reject' && request.method() === 'POST') {
      rejectCount += 1
      offerVisible = false
      return json({ ok: true, voice_session_id: 'wv_offer_1', status: 'ringing' })
    }
    if (url.pathname === '/api/webchat/admin/voice/wv_offer_1/accept' && request.method() === 'POST') {
      acceptCount += 1
      offerVisible = false
      return json({
        ok: true,
        voice_session_id: 'wv_offer_1',
        status: 'active',
        provider: 'livekit',
        livekit_url: null,
        participant_token: null,
        participant_identity: 'agent:9',
      })
    }
    return json({ detail: `Unhandled test API ${url.pathname}` }, 404)
  })

  return {
    rejectCount: () => rejectCount,
    acceptCount: () => acceptCount,
  }
}

test('assigned offer is visible globally and rejection rotates without accepting', async ({ page }) => {
  const state = await mockIncomingVoice(page)
  await page.goto('/workspace')

  await expect(page.getByRole('heading', { name: '新的语音来电' })).toBeVisible()
  await expect(page.getByText('Montenegro Caller')).toBeVisible()
  await expect(page.getByText('当前为实时会话，无需先创建工单。')).toBeVisible()

  await page.getByRole('button', { name: '暂不接听' }).click()
  await expect.poll(state.rejectCount).toBe(1)
  await expect.poll(state.acceptCount).toBe(0)
  await expect(page.getByRole('heading', { name: '新的语音来电' })).toHaveCount(0)
})

test('accepting an offer enters the sole WebCall route and calls accept once', async ({ page }) => {
  const state = await mockIncomingVoice(page)
  await page.goto('/workspace')

  await page.getByRole('button', { name: '接听通话' }).click()
  await expect(page).toHaveURL(/\/webcall\/wv_offer_1$/)
  await expect.poll(state.acceptCount).toBe(1)
  await expect.poll(state.rejectCount).toBe(0)
  await expect(page.getByRole('heading', { name: '来电上下文' })).toBeVisible()
  await expect(page.getByText('Montenegro Caller')).toBeVisible()
  await expect(page.getByRole('link', { name: '工作台' })).toBeVisible()
  await expect(page.getByText('LiveKit 会话凭证不可用')).toBeVisible()
})
