import { resolve } from 'node:path'
import { expect, test, type Route } from '@playwright/test'

async function json(route: Route, status: number, body: unknown): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test('a non-retryable WebSocket error falls back to polling without replacing the HTTP session', async ({ page }) => {
  const conversationId = 'wc_transport_authority'
  const visitorToken = 'visitor-token-transport-authority'
  const postedConversations: string[] = []
  let initCount = 0

  await page.addInitScript(() => {
    class MockWebSocket {
      static readonly CONNECTING = 0
      static readonly OPEN = 1
      static readonly CLOSING = 2
      static readonly CLOSED = 3
      readonly CONNECTING = 0
      readonly OPEN = 1
      readonly CLOSING = 2
      readonly CLOSED = 3
      readyState = MockWebSocket.CONNECTING
      onopen: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onclose: ((event: CloseEvent) => void) | null = null
      onerror: ((event: Event) => void) | null = null

      constructor(_url: string) {
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN
          this.onopen?.(new Event('open'))
        }, 0)
      }

      send(payload: string): void {
        const message = JSON.parse(payload) as { type?: string }
        if (message.type !== 'connection.hello') return
        setTimeout(() => {
          this.onmessage?.(new MessageEvent('message', {
            data: JSON.stringify({
              type: 'error',
              code: 'request_failed',
              message: 'realtime subscription rejected',
              retryable: false,
            }),
          }))
        }, 0)
      }

      close(): void {
        if (this.readyState === MockWebSocket.CLOSED) return
        this.readyState = MockWebSocket.CLOSED
        this.onclose?.(new CloseEvent('close', { code: 1000, reason: 'server_error' }))
      }
    }

    Object.defineProperty(window, 'WebSocket', {
      configurable: true,
      value: MockWebSocket,
    })
  })

  await page.route('**/webchat/widget.js', async (route) => {
    await route.fulfill({
      path: resolve(process.cwd(), '../backend/app/static/webchat/widget.js'),
      contentType: 'application/javascript',
    })
  })

  await page.route('**/api/webchat/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === '/api/webchat/init' && request.method() === 'POST') {
      initCount += 1
      await json(route, 200, {
        conversation_id: conversationId,
        visitor_token: visitorToken,
      })
      return
    }

    const match = url.pathname.match(/^\/api\/webchat\/conversations\/([^/]+)\/messages$/)
    if (!match) {
      await json(route, 404, { detail: 'unexpected_test_route' })
      return
    }

    if (request.method() === 'GET') {
      await json(route, 200, { messages: [], ai_pending: false })
      return
    }

    if (request.method() === 'POST') {
      const payload = request.postDataJSON() as { body: string; client_message_id?: string }
      postedConversations.push(match[1])
      await json(route, 200, {
        message: {
          id: postedConversations.length,
          direction: 'visitor',
          body_text: payload.body,
          client_message_id: payload.client_message_id,
        },
      })
      return
    }

    await json(route, 405, { detail: 'method_not_allowed' })
  })

  await page.goto('/')
  await page.setContent(`
    <!doctype html>
    <html>
      <body>
        <script
          src="/webchat/widget.js"
          data-auto-open="true"
          data-poll-ms="20"
          data-pending-poll-ms="20"
        ></script>
      </body>
    </html>
  `)

  const input = page.locator('.nd-webchat-input')
  const send = page.locator('.nd-webchat-send')
  await expect(input).toBeEnabled()
  await expect.poll(() => initCount).toBe(1)

  await input.fill('first message remains on the original session')
  await send.click()
  await expect(page.locator('.nd-webchat-msg.visitor', {
    hasText: 'first message remains on the original session',
  })).toBeVisible()

  await page.waitForTimeout(150)
  await input.fill('second message remains on the original session')
  await send.click()
  await expect(page.locator('.nd-webchat-msg.visitor', {
    hasText: 'second message remains on the original session',
  })).toBeVisible()

  expect(initCount).toBe(1)
  expect(postedConversations).toEqual([conversationId, conversationId])
  await expect(page.locator('.nd-webchat-msg.failed')).toHaveCount(0)
  await expect(page.locator('.nd-webchat-retry')).toHaveCount(0)
})
