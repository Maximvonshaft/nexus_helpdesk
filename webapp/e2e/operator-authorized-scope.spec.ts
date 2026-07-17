import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const SCOPE_KEY = 'nexus-operator-workspace-scope'

const authUser = {
  id: 91,
  username: 'scope-agent',
  display_name: 'Scope Agent',
  role: 'agent',
  capabilities: ['ticket.read', 'operator_queue.read'],
}

function queueResponse(country: string, channel: string) {
  return {
    items: [],
    next_cursor: null,
    scope: { tenant_hash: '123456789abc', country_code: country, channel_key: channel },
    filters: {
      state: 'active',
      source_type: null,
      owner: null,
      priority: null,
      sla: null,
      retry: null,
      sort: 'oldest',
    },
  }
}

async function seedSession(page: Page) {
  await page.addInitScript(([tokenKey, scopeKey]) => {
    sessionStorage.setItem(tokenKey, 'operator-token')
    sessionStorage.setItem(scopeKey, JSON.stringify({
      tenantKey: 'forged-stale-tenant',
      countryCode: 'ZZ',
      channelKey: 'unknown',
    }))
  }, [TOKEN_KEY, SCOPE_KEY])
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

const forgedStaleScope = {
  tenantKey: 'forged-stale-tenant',
  countryCode: 'ZZ',
  channelKey: 'unknown',
}

test('normal operators enter the canonical shell through a server-authorized scope', async ({ page }) => {
  await seedSession(page)
  let queueRequestSeen = false
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, authUser)
    if (url.pathname === '/api/admin/operator-queue/my-scopes') {
      return json(route, {
        items: [{
          tenant_key: 'tenant-authorized',
          tenant_hash: '123456789abc',
          country_code: 'CH',
          channel_key: 'webchat',
        }],
        requires_explicit_admin_scope: false,
      })
    }
    if (url.pathname === '/api/admin/operator-queue/unified') {
      queueRequestSeen = true
      expect(route.request().headers()['x-nexus-tenant']).toBe('tenant-authorized')
      expect(url.searchParams.get('country_code')).toBe('CH')
      expect(url.searchParams.get('channel_key')).toBe('webchat')
      return json(route, queueResponse('CH', 'webchat'))
    }
    return json(route, { detail: `Unhandled test API ${url.pathname}` }, 404)
  })

  await page.goto('/workspace')

  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  await expect(page.getByLabel('Nexus OSR')).toBeVisible()
  await expect(page.getByLabel('当前工作范围')).toHaveText('CH · 网页客服')
  await expect(page.locator('.operator-app-header')).toHaveCount(0)
  await expect(page.locator('.operator-scope')).toHaveCount(0)
  await expect(page.getByTestId('operator-workspace')).toBeVisible()
  await expect.poll(() => queueRequestSeen).toBe(true)
  await expect.poll(() => page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) || '{}'), SCOPE_KEY)).toEqual(forgedStaleScope)
})

test('switching among multiple authorized scopes remounts the workspace with the selected grant', async ({ page }) => {
  await seedSession(page)
  const seen: string[] = []
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, authUser)
    if (url.pathname === '/api/admin/operator-queue/my-scopes') {
      return json(route, {
        items: [
          {
            tenant_key: 'tenant-ch',
            tenant_hash: 'aaaaaaaaaaaa',
            country_code: 'CH',
            channel_key: 'webchat',
          },
          {
            tenant_key: 'tenant-me',
            tenant_hash: 'bbbbbbbbbbbb',
            country_code: 'ME',
            channel_key: 'whatsapp',
          },
        ],
        requires_explicit_admin_scope: false,
      })
    }
    if (url.pathname === '/api/admin/operator-queue/unified') {
      const tenant = route.request().headers()['x-nexus-tenant'] || ''
      const country = url.searchParams.get('country_code') || ''
      const channel = url.searchParams.get('channel_key') || ''
      seen.push(`${tenant}:${country}:${channel}`)
      return json(route, queueResponse(country, channel))
    }
    return json(route, { detail: `Unhandled test API ${url.pathname}` }, 404)
  })

  await page.goto('/workspace')
  const selector = page.getByRole('combobox', { name: '工作范围' })
  await expect(selector).toBeVisible()
  await expect(selector).toHaveText('CH · 网页客服')
  await expect.poll(() => seen.includes('tenant-ch:CH:webchat')).toBe(true)

  await selector.click()
  await page.getByRole('option', { name: 'ME · WhatsApp' }).click()

  await expect(selector).toHaveText('ME · WhatsApp')
  await expect.poll(() => seen.includes('tenant-me:ME:whatsapp')).toBe(true)
  await expect.poll(() => page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) || '{}'), SCOPE_KEY)).toEqual(forgedStaleScope)
  expect(seen).not.toContain('forged-stale-tenant:ZZ:unknown')
})

test('an unscoped normal operator receives a clear fail-closed state instead of free-text authority fields', async ({ page }) => {
  await seedSession(page)
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/auth/me') return json(route, authUser)
    if (url.pathname === '/api/admin/operator-queue/my-scopes') {
      return json(route, { items: [], requires_explicit_admin_scope: false })
    }
    return json(route, { detail: `Unhandled test API ${url.pathname}` }, 404)
  })

  await page.goto('/workspace')

  await expect(page.getByRole('heading', { name: '未分配工作范围' })).toBeVisible()
  await expect(page.getByText('请联系管理员。')).toBeVisible()
  await expect(page.locator('.operator-scope')).toHaveCount(0)
  await expect(page.getByTestId('operator-workspace')).toHaveCount(0)
})
