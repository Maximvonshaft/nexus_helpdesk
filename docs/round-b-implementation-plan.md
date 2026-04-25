# NexusDesk Round B Implementation Plan

## Added files

- `backend/app/webchat_models.py`
- `backend/app/api/webchat.py`
- `backend/app/services/webchat_service.py`
- `backend/app/static/webchat/widget.js`
- `backend/app/static/webchat/demo.html`
- `backend/alembic/versions/20260425_round_b_webchat.py`
- `backend/tests/test_webchat_round_b.py`
- `webapp/src/routes/webchat.tsx`
- `scripts/smoke/smoke_webchat_round_b.sh`
- `docs/webchat-widget.md`
- `docs/round-b-readonly-audit.md`
- `docs/round-b-implementation-plan.md`
- `docs/round-b-delivery-report.md`
- `docs/round-b-operator-demo-script.md`
- `docs/round-b-self-audit.md`

## Modified files

- `backend/app/main.py`
- `webapp/src/router.tsx`
- `webapp/src/layouts/AppShell.tsx`
- `webapp/src/lib/api.ts`
- `webapp/src/lib/types.ts` appends Webchat types through the apply script

## Alembic migration

Required. Revision `20260425_round_b_webchat` creates `webchat_conversations` and `webchat_messages` without modifying existing core ticket tables.

## Data model design

`webchat_conversations` stores the public conversation id, hashed visitor token, tenant/channel key, linked ticket id, visitor profile, origin/page/user-agent metadata, status, and timestamps.

`webchat_messages` stores visitor/agent/system messages linked to both conversation and ticket, including safety metadata for outbound replies.

## Public API design

- `POST /api/webchat/init`
- `POST /api/webchat/conversations/{conversation_id}/messages`
- `GET /api/webchat/conversations/{conversation_id}/messages?visitor_token=...`

Public APIs validate body length, token ownership, empty message rejection, and simple in-memory rate limits.

## Admin API design

- `GET /api/webchat/admin/conversations`
- `GET /api/webchat/admin/tickets/{ticket_id}/thread`
- `POST /api/webchat/admin/tickets/{ticket_id}/reply`

Admin APIs require the existing authenticated user dependency and reuse ticket visibility checks.

## Frontend UI plan

Add `/webchat` route with:

- Conversation list
- Source/visitor/page metadata
- Thread display
- Manual reply composer
- Fact evidence toggle
- Review confirmation toggle
- Safety gate feedback through error messages

## Widget design

Static vanilla JS widget:

- Floating button
- Responsive chat panel
- Visitor message form
- Local storage conversation restoration
- Polling for agent replies
- No backend token exposure
- No OpenClaw wording

## Demo page

`/webchat/demo.html` simulates a customer website embedding `/webchat/widget.js`.

## Safety integration

All admin replies call `evaluate_outbound_safety(...)` before writing the agent message. Blocked replies are rejected. Review replies require explicit `confirm_review=true`.

## Test plan

- Init conversation
- Send visitor message
- Poll own messages
- Reject invalid visitor token
- Admin list/thread/reply
- Safety allow/review/block

## Smoke plan

`smoke_webchat_round_b.sh` performs the full closure against a running environment and supports `BASE_URL`, `NEXUSDESK_DEV_USER_ID`, or `NEXUSDESK_ADMIN_TOKEN`.

## Rollback plan

1. Restore backed-up files from `.roundb_backup_<timestamp>`.
2. If migration applied and rollback is required, run `alembic downgrade 20260421_gov_r4` after confirming Webchat test data can be discarded.
3. Rebuild/restart app and worker.
4. Verify `/healthz` and `/readyz`.

## Risks

- Public CORS remains widget-friendly for Round B and should be tightened with domain allowlists in Round C.
- Polling is simple but less efficient than SSE/WebSocket.
- Full validation must be run inside the real repository/runtime after applying the overlay.
