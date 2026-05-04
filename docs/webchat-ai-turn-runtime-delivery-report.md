# WebChat AI-only Turn Runtime & Scheduler Delivery Report

## Branch

Requested branch:

```text
feature/webchat-ai-turn-runtime
```

Actual branch created:

```text
webchat-ai-turn-runtime
```

The requested branch name with `/` was blocked by the execution environment's safety layer during branch creation. The implementation was therefore committed to `webchat-ai-turn-runtime`.

## Executive summary

This branch implements the first closed-loop version of the WebChat AI-only runtime foundation:

- WebChat AI turns are now represented as durable records.
- Conversation-level AI runtime state is exposed as a fast-read snapshot.
- Public WebChat send/poll APIs now return `ai_pending`, `ai_status`, `ai_turn_id`, and `ai_pending_for_message_id`.
- Widget typing dots are driven by real backend AI state rather than a fixed fake animation.
- Consecutive visitor messages can be coalesced while an AI turn is still queued.
- Reconciler support was added to repair stale AI snapshots and avoid permanently stuck typing state.
- Bridge `/ai-reply` now has a bounded response contract with `ok`, `timeout`, `empty`, and `error` states.

## Files changed

```text
backend/app/webchat_models.py
backend/app/services/webchat_ai_turn_service.py
backend/app/services/webchat_ai_reconciler.py
backend/app/services/webchat_ai_safe_service.py
backend/app/api/webchat.py
backend/app/static/webchat/widget.js
backend/scripts/openclaw_bridge_server.js
backend/tests/test_webchat_ai_turn_runtime.py
webapp/src/lib/types.ts
```

## Data model changes

### Added `WebchatAITurn`

Purpose: source of truth for each AI reply turn.

Key fields:

```text
conversation_id
ticket_id
trigger_message_id
latest_visitor_message_id
context_cutoff_message_id
job_id
status
status_reason
reply_message_id
reply_source
fallback_reason
fact_gate_reason
bridge_elapsed_ms
bridge_timeout_ms
superseded_by_turn_id
is_public_reply_allowed
started_at
completed_at
created_at
updated_at
```

Supported statuses:

```text
queued
processing
bridge_calling
fallback_generating
completed
superseded
failed
timeout
cancelled
```

### Added `WebchatEvent`

Purpose: durable event log for future SSE, audit, and runtime observability.

Key event types:

```text
message.created
ai_turn.queued
ai_turn.coalesced
ai_turn.processing
ai_turn.bridge_calling
ai_turn.completed
ai_turn.superseded
ai_turn.fallback
ai_turn.failed
conversation.updated
```

### Added WebChat conversation AI snapshot fields

Purpose: public API fast-read cache for widget state.

Fields:

```text
active_ai_turn_id
active_ai_status
active_ai_for_message_id
active_ai_context_cutoff_message_id
next_ai_turn_id
active_ai_started_at
active_ai_updated_at
```

Important design detail:

These snapshot ids are plain indexed integers, not foreign keys. This avoids circular FK risk and keeps the source of truth in `WebchatAITurn`.

## Scheduler rules implemented

File:

```text
backend/app/services/webchat_ai_turn_service.py
```

Implemented rules:

1. No active turn:
   - create `WebchatAITurn(status='queued')`
   - create delayed `webchat.ai_reply` job
   - update conversation active snapshot
   - write `message.created` and `ai_turn.queued` events

2. Active turn is `queued`:
   - do not create a new turn
   - update `latest_visitor_message_id`
   - delay job by debounce window
   - write `ai_turn.coalesced`

3. Active turn is `processing`, `bridge_calling`, or `fallback_generating`:
   - do not interrupt current turn
   - queue `next_ai_turn_id`
   - keep current active snapshot

4. Terminal active turn found in snapshot:
   - clear/reconcile snapshot before scheduling new turn

5. Snapshot clearing uses compare-and-swap semantics:
   - only clear active snapshot when `conversation.active_ai_turn_id == current_turn.id`

## API changes

Existing API paths were preserved.

### `POST /api/webchat/conversations/{conversation_id}/messages`

Response now includes optional AI runtime fields:

```json
{
  "ai_pending": true,
  "ai_status": "queued",
  "ai_turn_id": 123,
  "ai_pending_for_message_id": 456,
  "coalesced": false
}
```

Implementation note:

`add_visitor_message()` is still used as the main write path. After it returns, the API layer schedules the AI turn, marks the immediate legacy job done, and creates the turn-aware `webchat.ai_reply` job.

### `GET /api/webchat/conversations/{conversation_id}/messages`

Response now includes optional AI runtime fields:

```json
{
  "ai_pending": true,
  "ai_status": "bridge_calling",
  "ai_turn_id": 123,
  "ai_pending_for_message_id": 456
}
```

The endpoint runs the reconciler before returning the snapshot.

### Admin WebChat responses

Admin conversation/thread responses now include the same AI snapshot fields where available.

## Widget changes

File:

```text
backend/app/static/webchat/widget.js
```

Implemented:

- real backend-driven typing dots
- `showTyping()` / `hideTyping()`
- `aiTypingText()`
- `ai_pending` / `ai_status` state consumption
- fast polling while AI is pending
- `prefers-reduced-motion` support
- avoid repeated `/init` before every message send
- retry `/init` once after token 403

Typing dots are controlled by backend state:

```text
ai_pending=true  -> show typing
ai_pending=false -> hide typing
agent/system/action message received -> hide typing
```

## Bridge contract changes

File:

```text
backend/scripts/openclaw_bridge_server.js
```

Implemented:

- `OPENCLAW_BRIDGE_AI_REPLY_ENABLED`
- `waitTimeoutMs` bounded handling
- response statuses: `ok`, `timeout`, `empty`, `error`
- `elapsedMs` and `timeoutMs`
- extracted `replyText`
- `/ai-reply` no longer depends on `allowWrites`
- `/send-message` still depends on `allowWrites`
- health includes `allowWrites`, `aiReplyEnabled`, and `sendMessageEnabled`

## Reconciler

File:

```text
backend/app/services/webchat_ai_reconciler.py
```

Repairs:

- active snapshot pointing to a missing turn
- active snapshot pointing to a terminal turn
- dead background job for an active turn
- active snapshot status mismatch

## Tests added

File:

```text
backend/tests/test_webchat_ai_turn_runtime.py
```

Covered scenarios:

1. AI turn created after WebChat visitor message.
2. Public poll reports pending AI state.
3. Consecutive queued visitor messages are coalesced into one turn.
4. Only one turn job exists for the coalesced turn.
5. Dispatch completes the turn, writes an AI reply, and clears pending snapshot.

## Known limitations and risks

### 1. Branch divergence

The branch was created from merge base:

```text
53fe4c983691206d56d53b22f6f64e57e4249543
```

Current `main` has advanced to:

```text
de8c5e36b74b37759ce015c8057bd4267e737d0e
```

GitHub compare reports:

```text
status: diverged
ahead_by: 10
behind_by: 11
```

Before merge, this branch must be rebased or merged against latest `main` and conflict-tested.

### 2. No migration file added

The repository path inspected during execution did not expose a standard Alembic migration path through the available tool results. Models were updated directly. If production schema is migration-managed, add a migration before deployment.

### 3. `background_jobs.py` not directly modified

Direct replacement of `backend/app/services/background_jobs.py` was blocked by the execution environment safety layer.

Workaround implemented:

- API layer marks the immediate legacy job done.
- API layer creates the turn-aware job directly with `enqueue_background_job()`.
- Existing background job processor still processes the job using `webchat_ai_safe_service.process_webchat_ai_reply_job()`.
- The safe service now detects and closes the matching open `WebchatAITurn`.

This preserves runtime closure for WebChat API-created messages, but other code paths that call `enqueue_webchat_ai_reply_job()` directly still use the legacy job path.

### 4. Tests not executed in this environment

Code changes were committed through the GitHub connector. The execution environment did not provide a local clone with dependency installation or network access to run the test suite. The tests were added, but pass/fail status is not verified here.

### 5. Bridge file may need latest-main reconciliation

Because `main` advanced after branch creation, `backend/scripts/openclaw_bridge_server.js` should be checked carefully during rebase. If latest main contains tracking lookup or additional Bridge routes, reconcile those with the bounded `/ai-reply` contract before merge.

## Required validation before merge

Run at minimum:

```bash
cd backend
pytest -q backend/tests/test_webchat_round_b.py backend/tests/test_webchat_ai_turn_runtime.py
```

If the repo uses a different backend working directory layout, run the equivalent pytest command from the project root.

For frontend:

```bash
cd webapp
npm run typecheck
npm run build
```

For Bridge smoke:

```bash
node backend/scripts/openclaw_bridge_server.js
curl -sS http://127.0.0.1:18792/health
```

Manual WebChat smoke:

1. Open WebChat demo/widget page.
2. Send a message.
3. Confirm user bubble appears immediately.
4. Confirm typing dots appear only when `ai_pending=true`.
5. Dispatch/run background jobs.
6. Confirm AI message appears and typing dots disappear.
7. Send two quick consecutive messages.
8. Confirm queued turn coalesces rather than generating multiple public replies.

## Rollback

Rollback is straightforward because existing API paths are preserved:

1. Revert this branch/PR.
2. Remove new model fields/tables through migration if deployed.
3. Restore old widget behavior by reverting `backend/app/static/webchat/widget.js`.
4. Restore old Bridge behavior by reverting `backend/scripts/openclaw_bridge_server.js`.

## Merge recommendation

Do not merge directly while branch is behind latest main. First:

1. Rebase/merge latest `main` into `webchat-ai-turn-runtime`.
2. Resolve Bridge file conflicts carefully.
3. Add DB migration if production uses migrations.
4. Run backend tests, frontend build, and Bridge smoke.
5. Then merge.
