import type { Page, Route } from '@playwright/test'

export const TOKEN_KEY = 'helpdesk-webapp-token'

export const responsiveUser = {
  id: 1,
  username: 'responsive-admin',
  display_name: 'Responsive Operations Administrator',
  email: 'responsive@example.test',
  role: 'admin',
  team_id: null,
  must_change_password: false,
  mfa_enabled: false,
  last_login_at: '2026-07-24T05:00:00Z',
  password_changed_at: '2026-07-20T05:00:00Z',
  capabilities: [
    'ticket.read',
    'ticket.assign',
    'operator_queue.read',
    'webchat.handoff.accept',
    'webcall.voice.read',
    'webcall.voice.queue.view',
    'webcall.voice.accept',
    'webcall.voice.reject',
    'webcall.voice.end',
    'webcall.voice.control',
    'ai_config.read',
    'ai_config.manage',
    'channel_account.manage',
    'runtime.manage',
    'audit.read',
    'security.read',
    'user.manage',
    'market.manage',
  ],
}

export const responsiveAgentState = {
  user_id: 1,
  status: 'online',
  heartbeat_fresh: true,
  assignable: true,
  max_concurrent_conversations: 4,
  active_conversations: 1,
  available_capacity: 3,
  voice_enabled: true,
  voice_assignable: true,
  max_concurrent_voice_calls: 1,
  active_voice_calls: 0,
  reserved_voice_offers: 0,
  available_voice_capacity: 1,
  voice_wrap_up_seconds: 30,
  last_heartbeat_at: '2026-07-24T05:00:00Z',
  heartbeat_ttl_seconds: 90,
}

export function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

export async function fulfillResponsiveApi(route: Route) {
  const url = new URL(route.request().url())
  const path = url.pathname

  if (path === '/api/auth/me') return json(route, responsiveUser)
  if (path === '/api/operator/agent-state') return json(route, responsiveAgentState)
  if (path === '/api/operator/agent-state/heartbeat') return json(route, responsiveAgentState)
  if (path === '/api/webchat/admin/voice/sessions') return json(route, { items: [] })
  if (path === '/api/observability/frontend-metrics') return route.fulfill({ status: 204 })
  if (path === '/api/admin/operator-queue/my-scopes') {
    return json(route, {
      items: [{ tenant_key: 'default', tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' }],
      requires_explicit_admin_scope: false,
    })
  }
  if (path === '/api/admin/operator-queue/unified') {
    return json(route, {
      items: [],
      next_cursor: null,
      scope: { tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' },
      filters: { state: 'active', source_type: null, owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
    })
  }
  if (path === '/api/agent-control/snapshot') {
    return json(route, {
      generated_at: Date.now() / 1000,
      tenant_key: 'default',
      scope: { environment: 'production', market_id: null, channel: 'webchat', language: null, case_type: null },
      definitions: [], releases: [], deployments: [], resolved_agent: null, resolved_agent_digest: null,
      resolution_error: 'agent_deployment_not_found', personas: [], persona_total: 0, knowledge: [], resources: [],
      resolved_playbooks: [], tools: [], tool_policies: [], integrations: [],
      capabilities: { can_manage: true, can_deploy: true, playground_model_execution: false },
    })
  }
  if (path === '/api/lite/knowledge-studio') return json(route, { kpis: [] })
  if (path === '/api/knowledge-items' && route.request().method() === 'GET') return json(route, { items: [], total: 0 })
  if (path === '/api/admin/channel-accounts') return json(route, [])
  if (path === '/api/admin/channel-onboarding-tasks') return json(route, { items: [], total: 0 })
  if (path === '/api/admin/provider-runtime/status') {
    return json(route, {
      ok: true, status: 'ready', app_env: 'test', webchat_runtime_enabled: false,
      configured_provider: null, fallback_provider: null, warnings: [], boundary: {}, providers: [],
    })
  }
  if (path === '/api/support/conversations/metrics') {
    return json(route, { total: 0, needs_human: 0, ai_active: 0, by_channel: {}, runtime_latency: null })
  }
  if (path === '/api/lite/control-tower') {
    return json(route, {
      generated_at: '2026-07-24T05:00:00Z', role: 'admin', user_id: 1,
      capabilities: responsiveUser.capabilities, kpis: [], manager_actions: [], team_workload: [], channel_health: [],
      bulletin_impact: [], governance_lanes: [], template_blocks: [], facts: {},
    })
  }
  return json(route, { detail: `Unavailable responsive acceptance fixture: ${path}` }, 404)
}

export async function mockResponsiveConsole(page: Page) {
  await page.addInitScript(([storageKey, token]) => {
    window.sessionStorage.setItem(storageKey, token)
  }, [TOKEN_KEY, 'responsive-admin-token'])
  await page.route('**/api/**', fulfillResponsiveApi)
}

export const canonicalRoutes = [
  { path: '/workspace', title: '案例处理 · Nexus OSR', ready: (page: Page) => page.getByTestId('operator-workspace') },
  { path: '/knowledge', title: '知识与流程 · Nexus OSR', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '知识与流程' }) },
  { path: '/agent-control', title: '自动处理配置 · Nexus OSR', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '自动处理配置' }) },
  { path: '/channels', title: '渠道管理 · Nexus OSR', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '渠道管理' }) },
  { path: '/runtime', title: '系统运行 · Nexus OSR', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '系统运行' }) },
  { path: '/control-tower', title: '运营监控 · Nexus OSR', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '运营监控' }) },
  { path: '/administration', title: '系统管理 · Nexus OSR', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '系统管理' }) },
  { path: '/account', title: '账户设置 · Nexus OSR', ready: (page: Page) => page.getByRole('heading', { level: 1, name: '账户设置' }) },
] as const
