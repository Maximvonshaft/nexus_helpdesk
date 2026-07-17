import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const widget = fs.readFileSync(
  path.resolve(here, '../../backend/app/static/webchat/widget.js'),
  'utf8',
)

let server: http.Server
let baseURL = ''
const testPort = Number(process.env.LIVE_VOICE_TEST_PORT || 0)

test.beforeAll(async () => {
  server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url || '/', 'http://127.0.0.1')
    const json = (body: unknown) => {
      response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
      response.end(JSON.stringify(body))
    }

    if (requestUrl.pathname === '/webchat/widget.js') {
      response.writeHead(200, { 'Content-Type': 'application/javascript; charset=utf-8' })
      response.end(widget)
      return
    }
    if (request.method === 'POST' && requestUrl.pathname === '/api/webchat/init') {
      json({ conversation_id: 'voice-conversation', visitor_token: 'voice-token' })
      return
    }
    if (request.method === 'GET' && requestUrl.pathname === '/api/webchat/conversations/voice-conversation/messages') {
      json({ messages: [], ai_status: null, ai_pending: false })
      return
    }
    if (request.method === 'POST' && requestUrl.pathname === '/api/webchat/conversations/voice-conversation/live-voice/session') {
      json({ voice_session_id: 'voice-session', connection_ticket: 'voice-ticket' })
      return
    }

    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
    response.end(`<!doctype html><html><head></head><body>
      <script src="/webchat/widget.js"
        data-live-voice-mode="edge-card"
        data-live-voice-ws-path="/webchat/live/ws"
        data-websocket="false"
        data-auto-open="false"></script>
    </body></html>`)
  })

  await new Promise<void>((resolve) => server.listen(testPort, '127.0.0.1', resolve))
  const address = server.address()
  if (!address || typeof address === 'string') {
    throw new Error('browser smoke server did not bind')
  }
  baseURL = `http://127.0.0.1:${address.port}`
})

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()))
  })
})

async function installVoiceFakes(page: Page, mode = 'ok') {
  await page.addInitScript(({ selectedMode }) => {
    const testWindow = window as typeof window & Record<string, any>
    const events: string[] = []
    testWindow.__VOICE_EVENTS__ = events
    testWindow.__VOICE_TEST_MODE__ = selectedMode
    testWindow.__VOICE_VISIBILITY__ = 'visible'

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => testWindow.__VOICE_VISIBILITY__,
    })

    class FakePort {
      onmessage: ((event: { data: unknown }) => void) | null = null

      postMessage(message: { type: string }) {
        events.push(`port:${message.type}`)
      }

      close() {
        events.push('port:close')
      }
    }

    class FakeAudioWorkletNode {
      port: FakePort

      constructor(
        _context: unknown,
        name: string,
        options: { processorOptions: { frameSamples: number } },
      ) {
        events.push(`node:${name}:${options.processorOptions.frameSamples}`)
        this.port = new FakePort()
        testWindow.__VOICE_CAPTURE_NODE__ = this
      }

      connect() {
        events.push('capture:connect')
      }

      disconnect() {
        events.push('capture:disconnect')
      }
    }

    class FakeAudioContext {
      state = 'running'
      currentTime = 0
      destination = {}
      audioWorklet = {
        addModule: async (url: string) => {
          events.push(`module:${url}`)
          if (testWindow.__VOICE_TEST_MODE__ === 'module-fail') {
            throw new Error('AudioWorklet module failed')
          }
        },
      }

      constructor() {
        events.push('context:create')
        testWindow.__VOICE_AUDIO_CONTEXT__ = this
      }

      resume() {
        events.push('context:resume')
        return Promise.resolve()
      }

      close() {
        events.push('context:close')
        this.state = 'closed'
        return Promise.resolve()
      }

      createMediaStreamSource() {
        return {
          connect() {
            events.push('source:connect')
          },
          disconnect() {
            events.push('source:disconnect')
          },
        }
      }

      createBuffer(_channels: number, length: number, sampleRate: number) {
        return {
          duration: length / sampleRate,
          copyToChannel() {},
        }
      }

      createBufferSource() {
        return {
          connect() {},
          disconnect() {},
          start() {},
          stop() {},
          onended: null,
        }
      }
    }

    class FakeWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      url: string
      readyState = FakeWebSocket.CONNECTING
      sent: number[] = []
      binaryType = 'blob'
      onopen: (() => void) | null = null
      onmessage: ((event: { data: unknown }) => void) | null = null
      onerror: (() => void) | null = null
      onclose: (() => void) | null = null

      constructor(url: string) {
        events.push(`socket:${url}`)
        this.url = url
        testWindow.__VOICE_SOCKETS__ = testWindow.__VOICE_SOCKETS__ || []
        testWindow.__VOICE_SOCKETS__.push(this)
        queueMicrotask(() => {
          if (testWindow.__VOICE_TEST_MODE__ === 'socket-fail') {
            events.push('socket:error')
            this.onerror?.()
            return
          }
          this.readyState = FakeWebSocket.OPEN
          this.onopen?.()
        })
      }

      send(packet: ArrayBuffer) {
        this.sent.push(packet.byteLength)
        events.push(`send:${packet.byteLength}`)
      }

      close() {
        this.readyState = FakeWebSocket.CLOSED
        events.push('socket:close')
        this.onclose?.()
      }
    }

    const track = {
      stop() {
        events.push('track:stop')
      },
    }

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          events.push('getUserMedia')
          if (testWindow.__VOICE_TEST_MODE__ === 'permission-denied') {
            const error = new Error('denied')
            error.name = 'NotAllowedError'
            throw error
          }
          return { getTracks: () => [track] }
        },
      },
    })

    testWindow.AudioContext = FakeAudioContext
    testWindow.AudioWorkletNode = selectedMode === 'unsupported' ? undefined : FakeAudioWorkletNode
    testWindow.WebSocket = FakeWebSocket
  }, { selectedMode: mode })
}

async function openAndStart(page: Page) {
  await page.goto(baseURL)
  await page.locator('.nd-webchat-button').click()
  await expect(page.locator('.nd-webchat-panel')).toHaveAttribute('data-open', 'true')
  await page.locator('.nd-webchat-voice').click()
  await expect(page.locator('.nd-webchat-voice-panel')).toHaveAttribute('data-open', 'true')
  await page.locator('.nd-webchat-voice-start').click()
}

test('synchronous double activation cancels before allocating browser resources', async ({ page }) => {
  await installVoiceFakes(page)
  await page.goto(baseURL)
  await page.locator('.nd-webchat-button').click()
  await page.locator('.nd-webchat-voice').click()

  await page.evaluate(() => {
    const start = document.querySelector('.nd-webchat-voice-start') as HTMLButtonElement
    start.click()
    start.click()
  })

  await expect(page.locator('.nd-webchat-voice-status')).toHaveText('Voice stopped.')
  const events = await page.evaluate(() => (window as any).__VOICE_EVENTS__ as string[])
  expect(events.some((event) => (
    event === 'context:create' || event === 'getUserMedia' || event.startsWith('socket:')
  ))).toBe(false)
})

test('explicit start streams bounded PCM and hidden cleanup is deterministic', async ({ page }) => {
  await installVoiceFakes(page)
  await openAndStart(page)
  await expect(page.locator('.nd-webchat-voice-status')).toHaveText('Listening...')

  const order = await page.evaluate(() => (window as any).__VOICE_EVENTS__ as string[])
  expect(order.findIndex((entry) => entry.startsWith('module:'))).toBeLessThan(
    order.findIndex((entry) => entry.startsWith('socket:')),
  )
  expect(order.findIndex((entry) => entry.startsWith('socket:'))).toBeLessThan(
    order.indexOf('getUserMedia'),
  )

  const socketBoundary = await page.evaluate(() => {
    const url = new URL((window as any).__VOICE_SOCKETS__[0].url)
    return { host: url.host, pageHost: window.location.host, protocol: url.protocol }
  })
  expect(socketBoundary.host).toBe(socketBoundary.pageHost)
  expect(socketBoundary.protocol).toBe('ws:')

  await page.evaluate(() => {
    ;(window as any).__VOICE_CAPTURE_NODE__.port.onmessage({
      data: { type: 'pcm16', buffer: new ArrayBuffer(640) },
    })
  })
  await expect.poll(
    () => page.evaluate(() => (window as any).__VOICE_SOCKETS__[0].sent),
  ).toEqual([640])

  await page.evaluate(() => {
    ;(window as any).__VOICE_VISIBILITY__ = 'hidden'
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect.poll(
    () => page.evaluate(() => (window as any).__VOICE_EVENTS__),
  ).toEqual(expect.arrayContaining([
    'port:stop',
    'port:close',
    'capture:disconnect',
    'source:disconnect',
    'track:stop',
    'context:close',
    'socket:close',
  ]))

  const socketCountBefore = await page.evaluate(
    () => (window as any).__VOICE_SOCKETS__.length,
  )
  await page.evaluate(() => {
    ;(window as any).__VOICE_VISIBILITY__ = 'visible'
  })
  await page.locator('.nd-webchat-voice-start').click()
  await expect.poll(
    () => page.evaluate(() => (window as any).__VOICE_SOCKETS__.length),
  ).toBe(socketCountBefore + 1)
})

test('explicit stop releases every live voice resource', async ({ page }) => {
  await installVoiceFakes(page)
  await openAndStart(page)
  await expect(page.locator('.nd-webchat-voice-status')).toHaveText('Listening...')

  await page.locator('.nd-webchat-voice-start').click()
  await expect(page.locator('.nd-webchat-voice-status')).toHaveText('Voice stopped.')
  await expect(page.locator('.nd-webchat-voice-start')).toHaveText('Start')
  await expect.poll(
    () => page.evaluate(() => (window as any).__VOICE_EVENTS__),
  ).toEqual(expect.arrayContaining([
    'port:stop',
    'port:close',
    'capture:disconnect',
    'source:disconnect',
    'track:stop',
    'context:close',
    'socket:close',
  ]))
  expect(await page.evaluate(() => (window as any).__VOICE_SOCKETS__.length)).toBe(1)
})

test('socket failure fails closed before microphone permission', async ({ page }) => {
  await installVoiceFakes(page, 'socket-fail')
  await openAndStart(page)
  await expect(page.locator('.nd-webchat-voice-status')).toHaveText(
    'Voice start failed: Voice connection failed.',
  )
  const events = await page.evaluate(() => (window as any).__VOICE_EVENTS__ as string[])
  expect(events).toEqual(expect.arrayContaining(['socket:error', 'socket:close', 'context:close']))
  expect(events).not.toContain('getUserMedia')
  expect(await page.evaluate(() => (window as any).__VOICE_CAPTURE_NODE__)).toBeUndefined()
})

test('permission denial fails closed and releases the socket', async ({ page }) => {
  await installVoiceFakes(page, 'permission-denied')
  await openAndStart(page)
  await expect(page.locator('.nd-webchat-voice-status')).toHaveText(
    'Microphone access was denied.',
  )
  await expect.poll(
    () => page.evaluate(() => (window as any).__VOICE_EVENTS__),
  ).toEqual(expect.arrayContaining(['socket:close', 'context:close']))
  expect(await page.evaluate(() => (window as any).__VOICE_CAPTURE_NODE__)).toBeUndefined()
})

test('module failure and unsupported browsers never request microphone permission', async ({ page }) => {
  await installVoiceFakes(page, 'module-fail')
  await openAndStart(page)
  await expect(page.locator('.nd-webchat-voice-status')).toHaveText(
    'AudioWorklet support is unavailable.',
  )
  expect(await page.evaluate(() => (
    (window as any).__VOICE_EVENTS__ as string[]
  ).some((event) => event === 'getUserMedia' || event.startsWith('socket:')))).toBe(false)

  const unsupported = await page.context().newPage()
  await installVoiceFakes(unsupported, 'unsupported')
  await openAndStart(unsupported)
  await expect(unsupported.locator('.nd-webchat-voice-status')).toHaveText(
    'AudioWorklet support is required for voice capture.',
  )
  expect(await unsupported.evaluate(() => (
    (window as any).__VOICE_EVENTS__ as string[]
  ).some((event) => event === 'getUserMedia' || event.startsWith('socket:')))).toBe(false)
  await unsupported.close()
})

test('oversized capture packets fail closed and pagehide releases resources', async ({ page }) => {
  await installVoiceFakes(page)
  await openAndStart(page)
  await expect(page.locator('.nd-webchat-voice-status')).toHaveText('Listening...')

  await page.evaluate(() => {
    ;(window as any).__VOICE_CAPTURE_NODE__.port.onmessage({
      data: { type: 'pcm16', buffer: new ArrayBuffer(4097) },
    })
  })
  await expect(page.locator('.nd-webchat-voice-status')).toContainText(
    'exceeded the safety limit',
  )
  expect(await page.evaluate(() => (window as any).__VOICE_SOCKETS__[0].sent)).toEqual([])

  await page.locator('.nd-webchat-voice-start').click()
  await expect(page.locator('.nd-webchat-voice-status')).toHaveText('Listening...')
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')))
  await expect.poll(
    () => page.evaluate(() => (
      (window as any).__VOICE_EVENTS__ as string[]
    ).filter((entry) => entry === 'track:stop').length),
  ).toBeGreaterThanOrEqual(2)
})
