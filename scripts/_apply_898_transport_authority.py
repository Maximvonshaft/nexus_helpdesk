from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one target, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


widget = "backend/app/static/webchat/widget.js"
replace_once(
    widget,
    "          if (data.code === 'request_failed' && data.retryable !== true) recoverLegacySession(session).catch(function () {});\n",
    "",
)
replace_once(
    "backend/app/static/webchat/demo/index.html",
    "webchat-session-recovery-v3",
    "webchat-session-recovery-v4",
)
replace_once(
    "backend/tests/test_webchat_ws_static_contracts.py",
    "webchat-session-recovery-v3",
    "webchat-session-recovery-v4",
)

static_path = Path("backend/tests/test_webchat_ws_static_contracts.py")
static_text = static_path.read_text(encoding="utf-8")
static_test = '''


def test_websocket_transport_error_falls_back_without_rotating_http_session():
    text = (ROOT / "backend" / "app" / "static" / "webchat" / "widget.js").read_text(encoding="utf-8")
    start = text.index("        if (data.type === 'error') {")
    end = text.index("        if (typeof data.event_id === 'number')", start)
    error_block = text[start:end]

    assert "recoverLegacySession" not in error_block
    assert "socket.close(1000, 'server_error')" in error_block
    close_start = text.index("      socket.onclose", start)
    close_end = text.index("      socket.onerror", close_start)
    assert "scheduleLegacyPoll();" in text[close_start:close_end]
'''
if "test_websocket_transport_error_falls_back_without_rotating_http_session" in static_text:
    raise SystemExit("static transport-authority test already exists")
static_path.write_text(static_text + static_test, encoding="utf-8")

browser_path = Path("webapp/e2e/webchat-realtime-fallback.spec.ts")
if browser_path.exists():
    raise SystemExit("webchat-realtime-fallback.spec.ts already exists")
browser_path.write_text(
    r'''import { resolve } from 'node:path'
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
''',
    encoding="utf-8",
)

capture_path = "scripts/release/capture_controlled_rc_failure.py"
status_loader = '''

def _load_status_stage(evidence_dir: Path) -> str | None:
    status_path = evidence_dir / "status.json"
    if not status_path.exists():
        return None
    if (
        not status_path.is_file()
        or status_path.is_symlink()
        or status_path.stat().st_size > MAX_SUMMARY_BYTES
    ):
        return None
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != "nexus.osr.rc-test-status.v1":
        return None
    stage = str(payload.get("stage") or "")
    return stage if _STAGE_RE.fullmatch(stage) else None
'''
replace_once(
    capture_path,
    "\n\ndef _bounded_service_states(value: object) -> dict[str, str]:\n",
    status_loader + "\n\ndef _bounded_service_states(value: object) -> dict[str, str]:\n",
)
replace_once(
    capture_path,
    '''    log_text = _read_bounded_text(log_path, max_bytes=MAX_LOG_BYTES)
    matches = _STAGE_LINE_RE.findall(log_text.replace("\\r\\n", "\\n"))
    stage = matches[-1] if matches else "unknown"

    evidence_dir.mkdir(parents=True, exist_ok=True)
''',
    '''    log_text = _read_bounded_text(log_path, max_bytes=MAX_LOG_BYTES)
    matches = _STAGE_LINE_RE.findall(log_text.replace("\\r\\n", "\\n"))

    evidence_dir.mkdir(parents=True, exist_ok=True)
    status_stage = _load_status_stage(evidence_dir)
    stage = status_stage or (matches[-1] if matches else "unknown")
''',
)

capture_test_path = Path("scripts/release/tests/test_capture_controlled_rc_failure.py")
capture_tests = capture_test_path.read_text(encoding="utf-8")
new_test = '''

    def test_capture_prefers_canonical_status_file_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "status.json").write_text(
                json.dumps({
                    "schema": "nexus.osr.rc-test-status.v1",
                    "stage": "browser-smoke",
                    "updated_at": "2026-08-01T00:00:00+00:00",
                }),
                encoding="utf-8",
            )
            log = root / "run.log"
            log.write_text("no RC_STAGE marker\\n", encoding="utf-8")

            output = MODULE.capture_failure(log_path=log, evidence_dir=evidence, exit_code=1)
            payload = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(payload["stage"], "browser-smoke")
            self.assertEqual(MODULE.validate_file(output), payload)
'''
marker = "\n    def test_capture_preserves_valid_existing_details(self) -> None:\n"
if capture_tests.count(marker) != 1:
    raise SystemExit("capture test insertion marker mismatch")
capture_test_path.write_text(capture_tests.replace(marker, new_test + marker, 1), encoding="utf-8")
