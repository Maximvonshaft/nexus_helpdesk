import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const CANONICAL_SOURCES = [
  'src/features/operator-workspace/lazy.tsx',
  'src/features/knowledge/lazy.tsx',
  'src/features/channels/lazy.tsx',
  'src/features/runtime/lazy.tsx',
  'src/features/control-tower/lazy.tsx',
  'src/features/administration/lazy.ts',
  'src/features/account/lazy.ts',
]

type ManifestRecord = {
  src?: string
  file: string
}

function manifest() {
  const manifestPath = resolve(process.cwd(), '../frontend_dist/.vite/manifest.json')
  return JSON.parse(readFileSync(manifestPath, 'utf8')) as Record<string, ManifestRecord>
}

function assetPath(sourceSuffix: string) {
  const pair = Object.entries(manifest()).find(([key, record]) => {
    const source = String(record.src || key).replaceAll('\\', '/')
    return source.endsWith(sourceSuffix)
  })
  if (!pair) throw new Error(`Missing manifest entry for ${sourceSuffix}`)
  return pair[1].file
}

const CANONICAL_ASSETS = CANONICAL_SOURCES.map(assetPath)

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

async function setAuthenticatedSession(page: Page) {
  await page.addInitScript(([key, token]) => sessionStorage.setItem(key, token), [TOKEN_KEY, 'route-split-token'])
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') {
      return json(route, {
        id: 1,
        username: 'operator',
        display_name: 'Operations User',
        role: 'admin',
        capabilities: [
          'ticket.read',
          'operator_queue.read',
          'ai_config.read',
          'ai_config.manage',
          'channel_account.manage',
          'runtime.manage',
          'audit.read',
          'ticket.assign',
          'user.manage',
        ],
      })
    }
    if (url.pathname === '/api/auth/security') {
      return json(route, {
        user_id: 1,
        session_version: 1,
        must_change_password: false,
        password_changed_at: null,
        last_login_at: '2026-07-20T10:00:00Z',
        updated_at: '2026-07-20T10:00:00Z',
      })
    }
    if (url.pathname === '/api/admin/operator-queue/my-scopes') {
      return json(route, {
        items: [{ tenant_key: 'tenant-route', tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' }],
        requires_explicit_admin_scope: false,
      })
    }
    if (url.pathname === '/api/admin/operator-queue/unified') {
      return json(route, {
        items: [],
        next_cursor: null,
        scope: { tenant_hash: '123456789abc', country_code: 'CH', channel_key: 'webchat' },
        filters: { state: 'active', source_type: null, owner: null, priority: null, sla: null, retry: null, sort: 'oldest' },
      })
    }
    if (url.pathname === '/api/lite/knowledge-studio') return json(route, { kpis: [] })
    if (url.pathname === '/api/knowledge-items') return json(route, { items: [], total: 0 })
    if (url.pathname === '/api/admin/channel-accounts') return json(route, [])
    if (url.pathname === '/api/admin/provider-runtime/status') {
      return json(route, { ok: true, status: 'ready', app_env: 'test', webchat_runtime_enabled: false, configured_provider: null, fallback_provider: null, warnings: [], providers: [], boundary: {} })
    }
    if (url.pathname === '/api/support/conversations/metrics') return json(route, { total: 0, needs_human: 0, ai_active: 0, by_channel: {} })
    return json(route, { detail: `Unhandled compatibility API ${url.pathname}` }, 404)
  })
}

test('unauthenticated webchat redirect does not request canonical protected route chunks', async ({ page }) => {
  let protectedChunkRequests = 0
  page.on('request', (request) => {
    if (CANONICAL_ASSETS.some((asset) => request.url().includes(`/${asset}`))) protectedChunkRequests += 1
  })

  await page.goto('/webchat')
  await expect(page).toHaveURL(/\/login$/)
  expect(protectedChunkRequests).toBe(0)
})

test('authenticated legacy conversation entry redirects to the canonical workspace', async ({ page }) => {
  await setAuthenticatedSession(page)
  await page.goto('/webchat')
  await expect(page).toHaveURL(/\/workspace(?:\?.*)?$/)
  await expect(page.getByTestId('operator-workspace')).toBeVisible()
})

for (const [query, destination, heading] of [
  ['tab=knowledge', '/knowledge', '知识与流程'],
  ['tab=channels', '/channels', '渠道管理'],
  ['tab=runtime', '/runtime', '系统运行'],
] as const) {
  test(`authenticated /webchat?${query} redirects to ${destination}`, async ({ page }) => {
    await setAuthenticatedSession(page)
    await page.goto(`/webchat?${query}`)
    await expect(page).toHaveURL(new RegExp(`${destination.replace('/', '\\/')}$`))
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
  })
}
