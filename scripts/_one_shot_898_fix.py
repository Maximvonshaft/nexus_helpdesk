from __future__ import annotations

import re
from pathlib import Path

ROOT = Path.cwd()


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def write(relative: str, content: str) -> None:
    (ROOT / relative).write_text(content, encoding="utf-8")


def replace_literal(relative: str, old: str, new: str) -> None:
    text = read(relative)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{relative}: expected one literal target, found {count}")
    write(relative, text.replace(old, new, 1))


def replace_pattern(relative: str, pattern: str, replacement: str) -> None:
    text = read(relative)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise SystemExit(f"{relative}: expected one regex target, found {count}")
    write(relative, updated)


widget_recovery = """  function hasLegacySession() {
    return Boolean(state.legacyConversationId && state.legacyVisitorToken);
  }

  function confirmLegacySessionReady() {
    if (!hasLegacySession()) throw new Error('webchat_session_recovery_failed');
    return true;
  }

  function joinLegacySessionTransition() {
    if (state.legacyRecoveryPromise) return state.legacyRecoveryPromise;
    if (state.legacySessionPromise) return state.legacySessionPromise.then(confirmLegacySessionReady);
    if (hasLegacySession()) return Promise.resolve(true);
    return createLegacySession().then(confirmLegacySessionReady);
  }

  function recoverLegacySession(expectedSession) {
    if (state.legacyRecoveryPromise) return state.legacyRecoveryPromise;
    if (expectedSession && !isLegacySessionCurrent(expectedSession)) {
      return joinLegacySessionTransition();
    }
    clearLegacySession();
    markReceiveDegraded('Reconnecting to support…');
    state.legacyRecoveryPromise = createLegacySession()
      .then(confirmLegacySessionReady)
      .finally(function () {
        state.legacyRecoveryPromise = null;
      });
    return state.legacyRecoveryPromise;
  }

  function rememberPublicSession"""

replace_pattern(
    "backend/app/static/webchat/widget.js",
    r"  function recoverLegacySession\(expectedSession\) \{\n.*?\n  \}\n\n  function rememberPublicSession",
    widget_recovery,
)
replace_literal(
    "backend/app/static/webchat/widget.js",
    "    var headers = { 'X-Webchat-Visitor-Token': state.legacyVisitorToken };",
    "    var headers = { 'X-Webchat-Visitor-Token': session.visitorToken };",
)
replace_literal(
    "backend/app/static/webchat/demo/index.html",
    "webchat-session-recovery-v2",
    "webchat-session-recovery-v3",
)

static_test = """def test_static_widget_declares_one_recovery_coordinator_and_no_stale_rejection_path():
    text = (ROOT / "backend" / "app" / "static" / "webchat" / "widget.js").read_text(encoding="utf-8")

    assert text.count("function recoverLegacySession(expectedSession)") == 1
    assert text.count("function joinLegacySessionTransition()") == 1
    assert "if (state.legacyRecoveryPromise) return state.legacyRecoveryPromise;" in text
    assert "if (state.legacySessionPromise) return state.legacySessionPromise.then(confirmLegacySessionReady);" in text
    assert "return joinLegacySessionTransition();" in text
    assert "Promise.resolve(false)" not in text
    assert "return recoverLegacySession(submittedSession).then(function (recovered)" in text
    assert "body: JSON.stringify({ body: body, client_message_id: cmid })" in text


def test_static_widget_ignores_stale_websocket_and_poll_callbacks"""

replace_pattern(
    "backend/tests/test_webchat_ws_static_contracts.py",
    r"def test_static_widget_recovers_stale_visitor_session_before_retrying_send\(\):\n.*?\n\n\ndef test_static_widget_ignores_stale_websocket_and_poll_callbacks",
    static_test,
)
replace_literal(
    "backend/tests/test_webchat_ws_static_contracts.py",
    "webchat-session-recovery-v2",
    "webchat-session-recovery-v3",
)

replace_literal(
    "webapp/e2e/rc-live.spec.ts",
    "const rcConfigured = Boolean(\n",
    "const rcRequired = (process.env.RC_RUN_BROWSER_SMOKE || '').toLowerCase() === 'true'\n"
    "const rcConfigured = Boolean(\n",
)

rc_start = """test.describe.configure({ mode: 'serial' })

test.describe('controlled candidate live WebChat', () => {
  test.skip(!rcRequired, 'RC live browser journey runs only in Controlled Candidate')

  test.beforeAll(() => {
    expect(
      rcConfigured,
      'RC_RUN_BROWSER_SMOKE=true requires admin credentials, an exact source SHA, and a loopback PLAYWRIGHT_BASE_URL',
    ).toBe(true)
  })

  test('RC public WebChat supports consecutive messages, human ownership, reply and closure', async ({ page, context }) => {
"""

replace_pattern(
    "webapp/e2e/rc-live.spec.ts",
    r"test\.describe\.configure\(\{ mode: 'serial' \}\)\n"
    r"test\.skip\(!rcConfigured, 'RC live browser environment is not configured'\)\n\n"
    r"test\('RC public WebChat supports consecutive messages, human ownership, reply and closure', async \(\{ page, context \}\) => \{\n",
    rc_start,
)
replace_pattern(
    "webapp/e2e/rc-live.spec.ts",
    r"  markStage\('completed'\)\n\}\)\s*$",
    "  markStage('completed')\n  })\n})\n",
)

recovery_spec = ROOT / "webapp/e2e/webchat-session-recovery.spec.ts"
if recovery_spec.exists():
    raise SystemExit("webapp/e2e/webchat-session-recovery.spec.ts already exists")
recovery_spec.write_text(
    r"""import { resolve } from 'node:path'
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
""",
    encoding="utf-8",
)

workflow = ".github/workflows/canonical-acceptance.yml"
replace_literal(
    workflow,
    """  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]
  push:
""",
    """  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]
  pull_request_review:
    types: [submitted, dismissed]
  push:
""",
)
replace_literal(workflow, "            pull_request)\n", "            pull_request|pull_request_review)\n")
replace_literal(
    workflow,
    """      - name: Verify frontend and browser journeys
        working-directory: webapp
        env:
          RC_RUN_BROWSER_SMOKE: "true"
        run: |
""",
    """      - name: Verify frontend and browser journeys
        working-directory: webapp
        run: |
""",
)

review_job = r"""  review-governance:
    name: review-governance
    if: >-
      needs.validation-mode.outputs.mode == 'candidate' &&
      (github.event_name == 'pull_request' || github.event_name == 'pull_request_review')
    needs: [validation-mode, candidate-identity]
    permissions:
      contents: read
      pull-requests: read
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - name: Require exact-Head independent approval and zero unresolved threads
        shell: bash
        env:
          GH_TOKEN: ${{ github.token }}
          REPOSITORY: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          PR_AUTHOR: ${{ github.event.pull_request.user.login }}
          EXPECTED_HEAD: ${{ needs.candidate-identity.outputs.source_sha }}
        run: |
          set -Eeuo pipefail
          [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]
          [[ "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]
          owner="${REPOSITORY%%/*}"
          name="${REPOSITORY#*/}"
          pull="$(gh api "repos/${REPOSITORY}/pulls/${PR_NUMBER}")"
          test "$(jq -r '.head.sha' <<<"$pull")" = "$EXPECTED_HEAD"
          test "$(jq -r '.draft' <<<"$pull")" = "false"
          reviews="$(gh api --paginate --slurp "repos/${REPOSITORY}/pulls/${PR_NUMBER}/reviews")"
          latest_reviews="$(jq -c --arg author "$PR_AUTHOR" '
            [
              .[][]
              | select(.user.login != $author)
              | select(
                  .state == "APPROVED"
                  or .state == "CHANGES_REQUESTED"
                  or .state == "DISMISSED"
                )
            ]
            | group_by(.user.login)
            | map(sort_by(.submitted_at) | last)
          ' <<<"$reviews")"
          test "$(jq --arg head "$EXPECTED_HEAD" '[.[] | select(.state == "APPROVED" and .commit_id == $head)] | length' <<<"$latest_reviews")" -ge 1
          test "$(jq '[.[] | select(.state == "CHANGES_REQUESTED")] | length' <<<"$latest_reviews")" = "0"
          threads="$(gh api graphql --paginate --slurp \
            -f owner="$owner" \
            -f name="$name" \
            -F number="$PR_NUMBER" \
            -f query='query($owner:String!,$name:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$endCursor){nodes{isResolved}pageInfo{hasNextPage endCursor}}}}}')"
          test "$(jq '[.[] | .data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length' <<<"$threads")" = "0"

"""

replace_literal(workflow, "  development-fast:\n", review_job + "  development-fast:\n")
replace_literal(
    workflow,
    """      - candidate-identity
      - development-fast
""",
    """      - candidate-identity
      - review-governance
      - development-fast
""",
)
replace_literal(
    workflow,
    """          MODE: ${{ needs.validation-mode.outputs.mode }}
          VALIDATION_MODE: ${{ needs.validation-mode.result }}
          CANDIDATE_IDENTITY: ${{ needs.candidate-identity.result }}
          DEVELOPMENT_FAST: ${{ needs.development-fast.result }}
""",
    """          MODE: ${{ needs.validation-mode.outputs.mode }}
          EVENT_NAME: ${{ github.event_name }}
          VALIDATION_MODE: ${{ needs.validation-mode.result }}
          CANDIDATE_IDENTITY: ${{ needs.candidate-identity.result }}
          REVIEW_GOVERNANCE: ${{ needs.review-governance.result }}
          DEVELOPMENT_FAST: ${{ needs.development-fast.result }}
""",
)
replace_literal(
    workflow,
    """            development)
              test "$DEVELOPMENT_FAST" = "success"
""",
    """            development)
              test "$REVIEW_GOVERNANCE" = "skipped"
              test "$DEVELOPMENT_FAST" = "success"
""",
)
replace_literal(
    workflow,
    """            candidate|main)
              test "$DEVELOPMENT_FAST" = "skipped"
""",
    """            candidate|main)
              if [[ "$MODE" = "candidate" && ( "$EVENT_NAME" = "pull_request" || "$EVENT_NAME" = "pull_request_review" ) ]]; then
                test "$REVIEW_GOVERNANCE" = "success"
              else
                test "$REVIEW_GOVERNANCE" = "skipped"
              fi
              test "$DEVELOPMENT_FAST" = "skipped"
""",
)

governance_path = "backend/tests/test_delivery_workflow_governance.py"
governance_text = read(governance_path)
test_name = "test_canonical_candidate_requires_completed_review_before_required_gate"
if test_name in governance_text:
    raise SystemExit(f"{governance_path}: {test_name} already exists")
governance_text += r'''


def test_canonical_candidate_requires_completed_review_before_required_gate():
    workflow = CANONICAL_WORKFLOW.read_text(encoding="utf-8")

    for marker in (
        "pull_request_review:",
        "review-governance:",
        "pull-requests: read",
        'select(.state == "APPROVED" and .commit_id == $head)',
        "reviewThreads(first:100,after:$endCursor)",
        "REVIEW_GOVERNANCE",
    ):
        assert marker in workflow

    assert 'RC_RUN_BROWSER_SMOKE: "true"' not in workflow
    assert "pull_request_target:" not in workflow
'''
write(governance_path, governance_text)

for temporary in (
    ".github/workflows/_one_shot_898_remediation.yml",
    ".github/workflows/_one_shot_898_pr_kick.yml",
    "scripts/_one_shot_898_fix.py",
):
    path = ROOT / temporary
    if path.exists():
        path.unlink()
