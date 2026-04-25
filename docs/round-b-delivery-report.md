# NexusDesk Round B Delivery Report

## Completed scope

Round B implements the first Webchat closure loop:

Website snippet → visitor message → Webchat ticket intake → admin Webchat inbox → safety-gated human reply → visitor polling sees reply.

## Modified / added files

- `backend/app/webchat_models.py`
- `backend/app/services/webchat_service.py`
- `backend/app/api/webchat.py`
- `backend/app/static/webchat/widget.js`
- `backend/app/static/webchat/demo.html`
- `backend/app/main.py`
- `backend/alembic/versions/20260425_round_b_webchat.py`
- `backend/tests/test_webchat_round_b.py`
- `webapp/src/routes/webchat.tsx`
- `webapp/src/router.tsx`
- `webapp/src/layouts/AppShell.tsx`
- `webapp/src/lib/api.ts`
- `webapp/src/lib/types.ts` appended with Round B Webchat types
- `scripts/smoke/smoke_webchat_round_b.sh`
- `docs/webchat-widget.md`
- `docs/round-b-delivery-report.md`
- `docs/round-b-operator-demo-script.md`

## Database migration

New revision:

```text
20260425_round_b_webchat
```

It creates:

- `webchat_conversations`
- `webchat_messages`

Rollback drops those two tables and their indexes.

## Test plan

Recommended commands after applying patch inside the real repository:

```bash
python -m compileall backend/app backend/scripts
cd backend && alembic upgrade head
cd backend && pytest -q tests/test_outbound_safety.py tests/test_webchat_round_b.py
cd ../webapp && npm ci && npm run typecheck && npm run build
cd .. && bash -n scripts/smoke/*.sh
BASE_URL=http://127.0.0.1:18081 NEXUSDESK_DEV_USER_ID=1 bash scripts/smoke/smoke_webchat_round_b.sh
```

## Smoke expected result

The smoke proves:

1. Public init returns `conversation_id` and `visitor_token`.
2. Visitor message is accepted.
3. Visitor can poll own messages.
4. Admin can locate Webchat ticket.
5. Sensitive reply is blocked by safety gate.
6. Safe reply is sent.
7. Visitor poll sees agent reply.

## Known limitations

- This package is an overlay patch generated from the available repository context and must be applied to the real repository before full build validation.
- Public CORS is intentionally widget-friendly in Round B. Round C should persist channel origin allowlists.
- Polling is intentionally chosen over WebSocket in Round B.
- OpenClaw suggested replies are not enabled in Round B.

## Round C proposal

1. Add Webchat channel configuration table with domain allowlists and brand settings.
2. Add OpenClaw suggested reply generation, but keep human approval by default.
3. Add SSE/WebSocket push for visitor messages.
4. Add widget analytics: open rate, first response time, abandonment.
5. Add per-tenant branding and per-market routing rules.
