import { resolve } from 'node:path'
import { expect, test, type Route } from '@playwright/test'

type Deferred = {
  promise: Promise<void>
  resolve: () => void
}

function deferred(): Deferred {
  let release: (() => void) | undefined
  const promise = new Promise<void>((resolvePromise) => {
    release = resolvePromise
  })
  return {
    promise,
    resolve: () => release?.(),
  }
}

async function json(route: Route, status: number, body: unknown): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test('a stale send joins concurrent recovery and retries exactly once on the recovered session', async ({ page }) => {
  const firstConversation = 'wc_recovery_original'
  const recoveredConversation = 'wc_recovery_replacement'
  const firstToken = 'visitor-token-original'
  const recoveredToken = 'visitor-token-replacement'
  const pollBlocked = deferred()
  const postBlocked = deferred()
  const recoveryInitBlocked = deferred()
  const pollStarted = deferred()
  const postStarted = deferred()
  const recoveryInitStarted = deferred()
  const retryPosted = deferred()
  let initCount = 0
  let expireOriginalSession = false
  const postedConversations: string[] = []
  const clientMessageIds: string[] = []

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
      if (initCount === 1) {
        await json(route, 200, {
          conversation_id: firstConversation,
          visitor_token: firstToken,
        })
        return
      }
      if (initCount === 2) {
        recoveryInitStarted.resolve()
        await recoveryInitBlocked.promise
        await json(route, 200, {
          conversation_id: recoveredConversation,
          visitor_token: recoveredToken,
        })
        return
      }
      throw new Error(`unexpected third session initialization: ${initCount}`)
    }

    const match = url.pathname.match(/^\/api\/webchat\/conversations\/([^/]+)\/messages$/)
    if (!match) {
      await json(route, 404, { detail: 'unexpected_test_route' })
      return
    }
    const conversationId = match[1]

    if (request.method() === 'GET') {
      if (conversationId === firstConversation && expireOriginalSession) {
        pollStarted.resolve()
        await pollBlocked.promise
        await json(route, 404, { detail: 'visitor_session_expired' })
        return
      }
      await json(route, 200, { messages: [], ai_pending: false })
      return
    }

    if (request.method() === 'POST') {
      const payload = request.postDataJSON() as { client_message_id?: string }
      postedConversations.push(conversationId)
      clientMessageIds.push(String(payload.client_message_id || ''))
      if (conversationId === firstConversation) {
        postStarted.resolve()
        await postBlocked.promise
        await json(route, 404, { detail: 'visitor_session_expired' })
        return
      }
      if (conversationId === recoveredConversation) {
        await json(route, 200, {
          message: {
            id: 101,
            direction: 'visitor',
            body_text: 'message survives concurrent recovery',
            client_message_id: payload.client_message_id,
          },
        })
        retryPosted.resolve()
        return
      }
    }

    await json(route, 405, { detail: 'method_not_allowed' })
  })

  await page.goto('/')
  await page.setContent(`
    <!doctype html>
    <html>
      <head></head>
      <body>
        <script
          src="/webchat/widget.js"
          data-auto-open="true"
          data-websocket="false"
          data-poll-ms="20"
          data-pending-poll-ms="20"
        ></script>
      </body>
    </html>
  `)

  await expect(page.locator('.nd-webchat-panel[data-open="true"]')).toBeVisible()
  const input = page.locator('.nd-webchat-input')
  const send = page.locator('.nd-webchat-send')
  await expect(input).toBeEnabled()
  await expect.poll(() => initCount).toBe(1)

  expireOriginalSession = true
  await pollStarted.promise
  await input.fill('message survives concurrent recovery')
  await send.click()
  await postStarted.promise

  pollBlocked.resolve()
  await recoveryInitStarted.promise
  postBlocked.resolve()
  await page.waitForTimeout(150)
  await expect(page.locator('.nd-webchat-msg.failed')).toHaveCount(0)
  await expect(page.locator('.nd-webchat-retry')).toHaveCount(0)

  recoveryInitBlocked.resolve()
  await retryPosted.promise

  await expect(page.locator('.nd-webchat-msg.visitor', {
    hasText: 'message survives concurrent recovery',
  })).toBeVisible()
  await expect(page.locator('.nd-webchat-msg.failed')).toHaveCount(0)
  await expect(page.locator('.nd-webchat-retry')).toHaveCount(0)
  await expect(send).toBeEnabled()
  expect(initCount).toBe(2)
  expect(postedConversations).toEqual([firstConversation, recoveredConversation])
  expect(clientMessageIds).toHaveLength(2)
  expect(clientMessageIds[0]).toBeTruthy()
  expect(clientMessageIds[1]).toBe(clientMessageIds[0])
})
