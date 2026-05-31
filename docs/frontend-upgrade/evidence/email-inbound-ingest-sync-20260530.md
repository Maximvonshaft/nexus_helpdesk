# Email Inbound Ingest / Sync Evidence

Date: 2026-05-30
Branch: `codex/email-inbound-ingest-sync`
Base: `codex/webcall-session-action-commands`

## Implemented

- Added `TicketInboundEmailMessage` plus migration `20260530_0043_email_inbound_messages.py`.
- Added `POST /api/tickets/{ticket_id}/email/inbound`.
- Inbound sync is `runtime.manage` gated and still requires ticket visibility.
- Inbound messages merge onto the ticket mailbox thread using provided `References` / `In-Reply-To` values and known outbound mailbox identities.
- Ingest writes ticket customer-message state, `TicketEvent(field_name=email.inbound)`, and `AdminAuditLog(email.inbound.ingested)` with body preview only.
- Duplicate provider/mailbox message ids return the existing row instead of creating another timeline/audit record.
- `/api/tickets/{ticket_id}/timeline` now returns `inbound_email` items with provider, mailbox thread/message ids, references, ticket event id, and audit id.
- `/email` uses `api.ingestInboundEmail` from `webapp/src/lib/api.ts` and renders inbound provider/mailbox evidence in the timeline.

## Local Validation

- `python -m compileall backend\app\models.py backend\app\schemas.py backend\app\services\email_inbound_service.py backend\app\api\tickets.py backend\app\api\ticket_perf.py backend\app\services\timeline_service.py backend\alembic\versions\20260530_0043_email_inbound_messages.py backend\tests\test_channel_workbench_backend_contracts.py` passed.
- `python -m pytest backend\tests\test_channel_workbench_backend_contracts.py backend\tests\test_email_outbound_runtime.py backend\tests\test_migration_drift_gate.py backend\tests\test_rbac_capability_contracts.py -q` passed: 21 passed, 22 warnings.
- `node --test webapp\tests\email-workbench-contract.test.mjs` passed: 9 passed.
- `npm test` in `webapp` passed: 83 passed.
- `npm run build` in `webapp` passed with existing LiveKit chunk-size warning.
- `npm run lint` in `webapp` passed with 0 errors and 5 existing hooks warnings.
- `git diff --check` passed.
- Browser smoke: `http://127.0.0.1:5174/email` redirects to `/login` when unauthenticated; no full-screen fixed overlay detected.

## Remaining Email Gaps

- Continuous mailbox polling/IMAP daemon is not implemented in this PR.
- Provider delivery receipt ingestion/UI remains separate from current sent/dead/retry provider status.
- Email queue source remains ticket metadata and markers, not an independent mailbox queue projection.
