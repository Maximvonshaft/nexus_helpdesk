import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test, type Page, type Route } from '@playwright/test'

const TOKEN_KEY = 'helpdesk-webapp-token'
const SUPPORT_SOURCE_SUFFIX = 'src/features/support-console/lazy.tsx'

type ManifestRecord = {
  src?: string
  file: string
}

function supportConsoleAssetPath() {
  const manifestPath = resolve(process.cwd(), '../frontend_dist/.vite/manifest.json')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as Record<string, ManifestRecord>
  const pair = Object.entries(manifest).find(([key, record]) => {
    const source = String(record.src || key).replaceAll('\\', '/')
    return source.endsWith(SUPPORT_SOURCE_SUFFIX)
  })
  if (!pair) throw new Error(`Missing manifest entry for ${SUPPORT_SOURCE_SUFFIX}`)
  return pair[1].file
}

const SUPPORT_ASSET_PATH = supportConsoleAssetPath()

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

async function fulfillConsoleApi(route: Route) {
  const url = new URL(route.request().url())
  if (url.pathname === '/api/auth/me') {
    return json(route, {
      id: 1,
      username: 'operator',
      display_name: 'Operations User',
      role: 'agent',
      capabilities: ['ticket.read'],
    })
  }
  if (url.pathname === '/api/support/conversations') {
    return json(route, {
      source: 'nexus_support_conversations',
      view: 'open',
      items: [{
        session_key: 'webchat:lazy-1',
        conversation_id: 'lazy-1',
        channel: 'webchat',
        source: 'webchat',
        ticket_id: 101,
        ticket_no: 'T-101',
        title: 'Lazy route case',
        status: 'open',
        conversation_state: 'ai_active',
        display_name: 'Lazy Route Customer',
        customer_contact: 'customer@example.test',
        updated_at: '2026-07-12T15:00:00Z',
        latest_message: 'Where is my parcel?',
        latest_author: 'customer',
        needs_human: false,
        handoff_status: 'none',
        ai_status: 'private_ai_runtime',
        ai_suspended: false,
        tracking_number_present: false,
        can_force_takeover: true,
        can_accept: false,
        can_release: false,
        can_resume_ai: false,
        can_reply: true,
      }],
    })
  }
  if (url.pathname === '/api/support/conversations/detail') {
    return json(route, {
      source: 'nexus_support_conversations',
      conversation: {
        session_key: 'webchat:lazy-1',
        conversation_id: 'lazy-1',
        channel: 'webchat',
        ticket_id: 101,
        ticket_no: 'T-101',
        title: 'Lazy route case',
        status: 'open',
        conversation_state: 'ai_active',
        display_name: 'Lazy Route Customer',
        customer_contact: 'customer@example.test',
        needs_human: false,
        handoff_status: 'none',
        ai_status: 'private_ai_runtime',
        ai_suspended: false,
        tracking_number_present: false,
        can_force_takeover: true,
        can_reply: true,
      },
      ticket: {
        id: 101,
        ticket_no: 'T-101',
        status: 'open',
        priority: 'normal',
        tracking_number_present: false,
      },
      messages: [
        { id: 'm-lazy-1', author: 'customer', body: 'Where is my parcel?', timestamp: '2026-07-12T15:00:00Z' },
      ],
      support_memory: {
        source: 'derived_support_memory_ledger',
        ticket: { id: 101, ticket_no: 'T-101', status: 'open' },
        conversation: { id: 'lazy-1', status: 'open', channel_key: 'webchat' },
        missing_fields: [],
        tracking: { present: false },
        ai_state: {},
        evidence_summary: {},
        evidence_timeline: [],
        next_actions: [],
      },
    })
  }
  if (url.pathname === '/api/support/conversations/state') {
    return json(route, {
      source: 'nexus_support_conversations',
      open: 1,
      requested_handoffs: 0,
      my_handoffs: 0,
      generated_at: '2026-07-12T15:00:00Z',
    })
  }
  return json(route, { detail: `Unhandled route-splitting API ${url.pathname}` }, 404)
}

async function setAuthenticatedSession(page: Page) {
  await page.addInitScript(([key, token]) => sessionStorage.setItem(key, token), [TOKEN_KEY, 'route-split-token'])
  await page.route('**/api/**', fulfillConsoleApi)
}

test('unauthenticated webchat redirect does not request the lazy console module', async ({ page }) => {
  let lazyModuleRequests = 0
  page.on('request', (request) => {
    if (request.url().includes(`/${SUPPORT_ASSET_PATH}`)) lazyModuleRequests += 1
  })

  await page.goto('/webchat')
  await expect(page).toHaveURL(/\/login$/)
  expect(lazyModuleRequests).toBe(0)
})

test('authenticated webchat announces loading then renders the async console', async ({ page }) => {
  await setAuthenticatedSession(page)
  await page.route(`**/${SUPPORT_ASSET_PATH}*`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 600))
    await route.continue()
  })

  await page.goto('/webchat', { waitUntil: 'domcontentloaded' })
  const loading = page.getByRole('status').filter({ hasText: '加载运营工作台中…' })
  await expect(loading).toBeVisible()
  await expect(page.getByTestId('nexus-support-console')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Lazy Route Customer' })).toBeVisible()
})
