from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path("/tmp/nexus-backend/generated-root-cause-patch")


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    assert count == 1, f"{label}: expected exactly one replacement, found {count}"
    return text.replace(old, new)


def _write(relative: str, content: str) -> None:
    target = OUTPUT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_generate_exact_root_cause_remediation_artifact() -> None:
    widget_path = ROOT / "backend/app/static/webchat/widget.js"
    widget = widget_path.read_text(encoding="utf-8")
    old_widget = '''  function clearLegacySession() {
    state.legacySessionGeneration += 1;
    if (state.legacyPollTimer) clearTimeout(state.legacyPollTimer);
    state.legacyPollTimer = null;
    if (state.legacyWsReconnectTimer) clearTimeout(state.legacyWsReconnectTimer);
    state.legacyWsReconnectTimer = null;
    try {
      if (state.legacyWs && state.legacyWs.readyState < WebSocket.CLOSING) state.legacyWs.close(1000, 'session_reset');
    } catch (err) {}
    state.legacyWs = null;
    state.legacyConversationId = null;
    state.legacyVisitorToken = null;
    state.legacyLastMessageId = 0;
    state.legacyLastEventId = 0;
    try { window.sessionStorage.removeItem(storageKey + ':legacy'); } catch (err) {}
  }

  function createLegacySession() {
    if (state.legacySessionPromise) return state.legacySessionPromise;
    setStatus('Connecting to support…', 'degraded');
    state.legacySessionPromise = api('/api/webchat/init', {
      method: 'POST',
      body: JSON.stringify({
        tenant_key: tenantKey,
        channel_key: channelKey,
        conversation_id: null,
        origin: window.location.origin,
        page_url: window.location.href
      })
    }, 12000).then(function (data) {
      state.legacyConversationId = data.conversation_id;
      state.legacyVisitorToken = data.visitor_token;
      persistLegacySession();
      markReceiveHealthy();
      startLegacyWs();
    }).catch(function (err) {
      markReceiveDegraded('Unable to connect to support. Retrying…');
      throw err;
    }).finally(function () {
      state.legacySessionPromise = null;
    });
    return state.legacySessionPromise;
  }

  function recoverLegacySession(expectedSession) {
    if (expectedSession && !isLegacySessionCurrent(expectedSession)) return Promise.resolve(false);
    if (state.legacyRecoveryPromise) return state.legacyRecoveryPromise;
    clearLegacySession();
    markReceiveDegraded('Reconnecting to support…');
    state.legacyRecoveryPromise = createLegacySession().then(function () {
      if (!state.legacyConversationId || !state.legacyVisitorToken) throw new Error('webchat_session_recovery_failed');
      markReceiveHealthy();
      return true;
    }).finally(function () {
      state.legacyRecoveryPromise = null;
    });
    return state.legacyRecoveryPromise;
  }
'''
    new_widget = '''  function stopLegacyTransports(closeReason) {
    state.legacySessionGeneration += 1;
    if (state.legacyPollTimer) clearTimeout(state.legacyPollTimer);
    state.legacyPollTimer = null;
    if (state.legacyWsReconnectTimer) clearTimeout(state.legacyWsReconnectTimer);
    state.legacyWsReconnectTimer = null;
    try {
      if (state.legacyWs && state.legacyWs.readyState < WebSocket.CLOSING) {
        state.legacyWs.close(1000, closeReason || 'session_reset');
      }
    } catch (err) {}
    state.legacyWs = null;
  }

  function clearLegacySession() {
    stopLegacyTransports('session_reset');
    state.legacyConversationId = null;
    state.legacyVisitorToken = null;
    state.legacyLastMessageId = 0;
    state.legacyLastEventId = 0;
    try { window.sessionStorage.removeItem(storageKey + ':legacy'); } catch (err) {}
  }

  function hasLegacySession() {
    return Boolean(state.legacyConversationId && state.legacyVisitorToken);
  }

  function createLegacySession(resumeSession) {
    if (state.legacySessionPromise) return state.legacySessionPromise;
    var resumable = Boolean(resumeSession && resumeSession.conversationId && resumeSession.visitorToken);
    var headers = resumable ? { 'X-Webchat-Visitor-Token': resumeSession.visitorToken } : {};
    setStatus(resumable ? 'Revalidating support session…' : 'Connecting to support…', 'degraded');
    state.legacySessionPromise = api('/api/webchat/init', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({
        tenant_key: tenantKey,
        channel_key: channelKey,
        conversation_id: resumable ? resumeSession.conversationId : null,
        origin: window.location.origin,
        page_url: window.location.href
      })
    }, 12000).then(function (data) {
      if (resumable && data.conversation_id !== resumeSession.conversationId) {
        var driftError = new Error('webchat_session_identity_drift');
        driftError.status = 409;
        throw driftError;
      }
      state.legacyConversationId = data.conversation_id;
      state.legacyVisitorToken = data.visitor_token;
      persistLegacySession();
      markReceiveHealthy();
      startLegacyWs();
    }).catch(function (err) {
      markReceiveDegraded(resumable ? 'Unable to restore this support session.' : 'Unable to connect to support. Retrying…');
      throw err;
    }).finally(function () {
      state.legacySessionPromise = null;
    });
    return state.legacySessionPromise;
  }

  function recoverLegacySession(expectedSession) {
    if (state.legacyRecoveryPromise) return state.legacyRecoveryPromise;
    if (expectedSession && !isLegacySessionCurrent(expectedSession)) {
      if (hasLegacySession()) return Promise.resolve(true);
      if (state.legacySessionPromise) {
        return state.legacySessionPromise.then(function () { return hasLegacySession(); });
      }
      return Promise.resolve(false);
    }
    var resumableSession = expectedSession || legacySessionSnapshot();
    if (!resumableSession.conversationId || !resumableSession.visitorToken) {
      return createLegacySession().then(function () { return hasLegacySession(); });
    }
    stopLegacyTransports('session_revalidate');
    markReceiveDegraded('Reconnecting to support…');
    var recovery = createLegacySession(resumableSession).then(function () {
      if (!hasLegacySession()) throw new Error('webchat_session_recovery_failed');
      markReceiveHealthy();
      return true;
    }).catch(function (err) {
      if (isLegacySessionAuthError(err) || (err && err.status === 409)) clearLegacySession();
      throw err;
    });
    state.legacyRecoveryPromise = recovery.finally(function () {
      state.legacyRecoveryPromise = null;
    });
    return state.legacyRecoveryPromise;
  }
'''
    widget = _replace_once(widget, old_widget, new_widget, label="widget recovery authority")
    assert "conversation_id: resumable ? resumeSession.conversationId : null" in widget
    assert "if (state.legacyRecoveryPromise) return state.legacyRecoveryPromise;" in widget
    assert "if (expectedSession && !isLegacySessionCurrent(expectedSession)) return Promise.resolve(false);" not in widget
    _write("backend/app/static/webchat/widget.js", widget)

    demo_path = ROOT / "backend/app/static/webchat/demo/index.html"
    demo = _replace_once(
        demo_path.read_text(encoding="utf-8"),
        "/webchat/widget.js?v=webchat-session-recovery-v2",
        "/webchat/widget.js?v=webchat-session-recovery-v3",
        label="demo cache identity",
    )
    _write("backend/app/static/webchat/demo/index.html", demo)

    static_path = ROOT / "backend/tests/test_webchat_ws_static_contracts.py"
    static_tests = static_path.read_text(encoding="utf-8").replace(
        "/webchat/widget.js?v=webchat-session-recovery-v2",
        "/webchat/widget.js?v=webchat-session-recovery-v3",
    )
    start = static_tests.index("\ndef test_static_widget_recovers_stale_visitor_session_before_retrying_send():")
    end = static_tests.index("\ndef test_webchat_ws_observability_and_connection_limits_contract():", start)
    static_tests = static_tests[:start] + "\n\n" + static_tests[end:]
    assert "test_static_widget_recovers_stale_visitor_session" not in static_tests
    assert "test_static_widget_ignores_stale_websocket" not in static_tests
    _write("backend/tests/test_webchat_ws_static_contracts.py", static_tests)

    config_path = ROOT / "webapp/playwright.config.ts"
    config = _replace_once(
        config_path.read_text(encoding="utf-8"),
        "  testDir: './e2e',\n  fullyParallel: true,",
        "  testDir: './e2e',\n  testIgnore: rcBrowser ? [] : ['**/rc-live.spec.ts'],\n  fullyParallel: true,",
        label="Playwright RC ownership",
    )
    _write("webapp/playwright.config.ts", config)

    rc_path = ROOT / "webapp/e2e/rc-live.spec.ts"
    rc_live = _replace_once(
        rc_path.read_text(encoding="utf-8"),
        "test.describe.configure({ mode: 'serial' })\ntest.skip(!rcConfigured, 'RC live browser environment is not configured')",
        "test.describe.configure({ mode: 'serial' })\ntest.beforeAll(() => {\n  expect(rcConfigured, 'RC live browser environment must be configured').toBe(true)\n})",
        label="RC fail-closed configuration",
    )
    _write("webapp/e2e/rc-live.spec.ts", rc_live)

    browser_test = '''import { readFileSync } from 'node:fs'
import { expect, test, type Route } from '@playwright/test'

const widgetSource = readFileSync(
  new URL('../../backend/app/static/webchat/widget.js', import.meta.url),
  'utf8',
)

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void
  const promise = new Promise<void>((done) => { resolve = done })
  return { promise, resolve }
}

async function fulfillJson(route: Route, status: number, payload: unknown): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) })
}

test('WebChat joins in-flight recovery and never changes conversation identity silently', async ({ page }) => {
  test.setTimeout(45_000)
  await page.clock.install()

  const conversationId = 'wc_original_session'
  const visitorToken = 'visitor_token_original_1234567890'
  const firstMessage = 'first deterministic message'
  const secondMessage = 'second deterministic message'
  const secondPostGate = deferred()
  const recoveryInitGate = deferred()
  const initPayloads: Array<Record<string, unknown>> = []
  const messagePaths: string[] = []
  const secondClientMessageIds: string[] = []
  let initCount = 0
  let pollCount = 0
  let secondPostAttempts = 0
  let secondPostStarted = false
  let recoveryInitStarted = false

  await page.route('**/api/webchat/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (request.method() === 'POST' && path === '/api/webchat/init') {
      initCount += 1
      const payload = request.postDataJSON() as Record<string, unknown>
      initPayloads.push(payload)
      if (initCount === 1) {
        expect(payload.conversation_id).toBeNull()
        await fulfillJson(route, 200, { conversation_id: conversationId, visitor_token: visitorToken, status: 'open' })
        return
      }
      expect(initCount).toBe(2)
      expect(payload.conversation_id).toBe(conversationId)
      expect(request.headers()['x-webchat-visitor-token']).toBe(visitorToken)
      recoveryInitStarted = true
      await recoveryInitGate.promise
      await fulfillJson(route, 200, { conversation_id: conversationId, visitor_token: visitorToken, status: 'open' })
      return
    }

    if (request.method() === 'POST' && /\/api\/webchat\/conversations\/[^/]+\/messages$/.test(path)) {
      messagePaths.push(path)
      const payload = request.postDataJSON() as { body?: string; client_message_id?: string }
      if (payload.body === firstMessage) {
        await fulfillJson(route, 200, { message: { id: 1, direction: 'visitor', body_text: firstMessage, client_message_id: payload.client_message_id } })
        return
      }
      expect(payload.body).toBe(secondMessage)
      secondPostAttempts += 1
      secondClientMessageIds.push(String(payload.client_message_id || ''))
      if (secondPostAttempts === 1) {
        secondPostStarted = true
        await secondPostGate.promise
        await fulfillJson(route, 404, { detail: 'transient session revalidation required' })
        return
      }
      expect(secondPostAttempts).toBe(2)
      await fulfillJson(route, 200, { message: { id: 2, direction: 'visitor', body_text: secondMessage, client_message_id: payload.client_message_id } })
      return
    }

    if (request.method() === 'GET' && /\/api\/webchat\/conversations\/[^/]+\/messages$/.test(path)) {
      pollCount += 1
      if (pollCount === 2) {
        await fulfillJson(route, 404, { detail: 'transient poll session error' })
        return
      }
      await fulfillJson(route, 200, { messages: [], ai_pending: false, ai_status: 'completed' })
      return
    }

    await fulfillJson(route, 404, { detail: `unexpected route ${request.method()} ${path}` })
  })

  await page.goto('/')
  await page.evaluate((source) => {
    document.body.innerHTML = ''
    delete (window as typeof window & { __NEXUSDESK_WEBCHAT_LOADED__?: boolean }).__NEXUSDESK_WEBCHAT_LOADED__
    const script = document.createElement('script')
    script.setAttribute('data-api-base', window.location.origin)
    script.setAttribute('data-auto-open', 'true')
    script.setAttribute('data-websocket', 'false')
    script.setAttribute('data-poll-ms', '1000')
    script.setAttribute('data-pending-poll-ms', '350')
    script.textContent = source
    document.head.appendChild(script)
  }, widgetSource)

  await page.clock.fastForward(200)
  await expect.poll(() => initCount).toBe(1)
  const input = page.locator('.nd-webchat-input')
  const send = page.locator('.nd-webchat-send')
  await expect(input).toBeEnabled()

  await input.fill(firstMessage)
  await send.click()
  await expect(page.locator('.nd-webchat-msg.visitor', { hasText: firstMessage })).toBeVisible()
  await expect(send).toBeEnabled()
  await expect.poll(() => pollCount).toBe(1)

  await input.fill(secondMessage)
  await send.click()
  await expect.poll(() => secondPostStarted).toBe(true)

  await page.clock.fastForward(1000)
  await expect.poll(() => recoveryInitStarted).toBe(true)
  expect(pollCount).toBeGreaterThanOrEqual(2)

  secondPostGate.resolve()
  await expect.poll(() => secondPostAttempts).toBe(1)
  recoveryInitGate.resolve()

  await expect.poll(() => secondPostAttempts).toBe(2)
  await expect(page.locator('.nd-webchat-msg.visitor', { hasText: secondMessage })).toBeVisible()
  await expect(page.locator('.nd-webchat-msg.visitor.failed', { hasText: secondMessage })).toHaveCount(0)
  await expect(page.locator('.nd-webchat-msg.visitor', { hasText: secondMessage }).locator('.nd-webchat-retry')).toHaveCount(0)

  expect(initPayloads).toHaveLength(2)
  expect(initPayloads[1]?.conversation_id).toBe(conversationId)
  expect(messagePaths).toHaveLength(3)
  expect(messagePaths.every((path) => path.includes(`/${conversationId}/messages`))).toBe(true)
  expect(secondClientMessageIds).toHaveLength(2)
  expect(secondClientMessageIds[0]).toBeTruthy()
  expect(secondClientMessageIds[1]).toBe(secondClientMessageIds[0])
})
'''
    _write("webapp/e2e/webchat-widget-session-recovery.spec.ts", browser_test)

    subprocess.run(
        ["node", "--check", str(OUTPUT / "backend/app/static/webchat/widget.js")],
        check=True,
        cwd=ROOT,
    )

    manifest = {}
    for path in sorted(OUTPUT.rglob("*")):
        if path.is_file():
            relative = path.relative_to(OUTPUT).as_posix()
            manifest[relative] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
    (OUTPUT / "manifest.json").write_text(
        json.dumps({"schema": "nexus.root-cause-patch.v1", "files": manifest}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert len(manifest) == 6
