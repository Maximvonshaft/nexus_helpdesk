import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page, type Route } from '@playwright/test'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const widgetLoader = readFileSync(resolve(ROOT, 'backend/app/static/webchat/widget.js'), 'utf8')
const widgetRuntime = readFileSync(resolve(ROOT, 'backend/app/static/webchat/widget-runtime.js'), 'utf8')

function listen(server: Server): Promise<number> {
  return new Promise((resolvePort, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      server.removeListener('error', reject)
      resolvePort((server.address() as AddressInfo).port)
    })
  })
}

function close(server: Server): Promise<void> {
  return new Promise((resolveClose, reject) => {
    server.close((error) => error ? reject(error) : resolveClose())
  })
}

async function fulfillJson(route: Route, customerOrigin: string, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    headers: {
      'Access-Control-Allow-Origin': customerOrigin,
      'Access-Control-Allow-Headers': 'Content-Type, X-Webchat-Visitor-Token, X-Webchat-WS-Fallback',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Cache-Control': 'no-store',
    },
    body: JSON.stringify(body),
  })
}

async function installNexusRoutes(page: Page, nexusOrigin: string, customerOrigin: string) {
  await page.route(`${nexusOrigin}/webchat/widget.js`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: widgetLoader })
  })
  await page.route(`${nexusOrigin}/webchat/widget-runtime.js`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: widgetRuntime })
  })
  await page.route(`${nexusOrigin}/api/webchat/**`, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() === 'OPTIONS') {
      await route.fulfill({
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': customerOrigin,
          'Access-Control-Allow-Headers': 'Content-Type, X-Webchat-Visitor-Token, X-Webchat-WS-Fallback',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        },
      })
      return
    }
    if (url.pathname === '/api/webchat/init') {
      await fulfillJson(route, customerOrigin, {
        conversation_id: 'wc_external',
        visitor_token: 'visitor-token',
        status: 'open',
        config: { poll_interval_ms: 4000, max_message_chars: 2000 },
      })
      return
    }
    if (url.pathname === '/api/webchat/conversations/wc_external/messages') {
      await fulfillJson(route, customerOrigin, { messages: [], ai_pending: false, ai_status: null })
      return
    }
    if (url.pathname === '/api/webchat/conversations/wc_external/voice/policy') {
      await fulfillJson(route, customerOrigin, {
        schema: 'nexus.voice-compliance-policy.v1',
        policy_version: 'nexus.voice-compliance.v1',
        recording: { capability: 'recording', policy: 'disabled' },
        transcript_persistence: { capability: 'transcript_persistence', policy: 'disabled' },
      })
      return
    }
    if (url.pathname === '/api/webchat/conversations/wc_external/voice/sessions') {
      await fulfillJson(route, customerOrigin, {
        ok: true,
        voice_session_id: 'wv_external',
        provider: 'livekit',
        livekit_url: 'wss://voice.example.test',
        participant_token: 'participant-token',
        participant_identity: 'visitor:wc_external',
      })
      return
    }
    await fulfillJson(route, customerOrigin, { detail: `Unhandled test API ${url.pathname}` }, 404)
  })
}

test('external WebChat embed opens WebCall on the server-owned Nexus origin when popup is blocked', async ({ page }, testInfo) => {
  const configuredBase = String(testInfo.project.use.baseURL || 'http://127.0.0.1:4173')
  const nexusOrigin = new URL(configuredBase).origin
  const host = createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
    response.end(`<!doctype html><html><head><meta charset="utf-8"><title>External customer host</title></head><body><h1>Customer site</h1><script src="${nexusOrigin}/webchat/widget.js" data-api-base="${nexusOrigin}" data-live-voice-mode="livekit-room" data-websocket="false"></script></body></html>`)
  })
  const port = await listen(host)
  const customerOrigin = `http://127.0.0.1:${port}`

  try {
    await installNexusRoutes(page, nexusOrigin, customerOrigin)
    await page.addInitScript(() => {
      window.open = () => null
    })
    await page.goto(customerOrigin)

    await page.getByRole('button', { name: 'Chat with us' }).click()
    const voiceButton = page.getByRole('button', { name: 'VOIP Call' })
    await expect(voiceButton).toBeVisible()
    await voiceButton.click()
    await page.getByRole('button', { name: 'Start call' }).click()

    await expect(page).toHaveURL(new RegExp(`^${nexusOrigin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/webcall/wv_external#`))
    const finalUrl = new URL(page.url())
    expect(finalUrl.origin).toBe(nexusOrigin)
    expect(finalUrl.origin).not.toBe(customerOrigin)
    expect(finalUrl.pathname).toBe('/webcall/wv_external')
  } finally {
    await close(host)
  }
})
