# Email Mailbox Queue Projection Evidence

Date: 2026-05-30
Branch: `codex/email-mailbox-queue-projection`
Base: `codex/email-delivery-receipts`

## Implemented

- Added `GET /api/email/queue` as an independent Email mailbox queue read model.
- Queue rows are projected from real `TicketInboundEmailMessage` rows, Email `TicketOutboundMessage` rows, and explicit Email ticket markers.
- The API enforces backend RBAC: `ticket.read` plus at least one of `outbound.draft.save` or `outbound.send`, then applies ticket visibility.
- Queue response includes mailbox thread/message ids, provider status, delivery status, direction, queue source, queue reason, customer/ticket context, SLA overdue state, and last mailbox subject/preview.
- `/email` now calls `api.emailMailboxQueue` through the unified frontend API client instead of filtering `/api/lite/cases` in the browser.
- The workbench queue renders `queue_reason`, `queue_source`, delivery status, subject, and mailbox identity as first-class queue evidence.

## Local Validation

- `python -m compileall backend\app\schemas.py backend\app\services\email_mailbox_queue_service.py backend\app\api\email.py backend\app\main.py backend\tests\test_channel_workbench_backend_contracts.py` passed.
- `node --test webapp\tests\email-workbench-contract.test.mjs` passed: 10 passed.
- `python -m pytest backend\tests\test_channel_workbench_backend_contracts.py -q` passed: 7 passed, 13 warnings.
- `python -m pytest backend\tests\test_channel_workbench_backend_contracts.py backend\tests\test_email_outbound_runtime.py backend\tests\test_migration_drift_gate.py backend\tests\test_rbac_capability_contracts.py -q` passed: 23 passed, 24 warnings.
- `npm test` passed: 84 passed.
- `npm run build` passed. Vite reported the existing `vendor-livekit` chunk size warning.
- `npm run lint` passed with 0 errors and 5 existing hook dependency warnings in unrelated files.
- `git diff --check` passed.
- Browser smoke used the Codex in-app Browser against `http://127.0.0.1:5174/email`; it redirected to `/login`, rendered a non-blank login page, had no framework error overlay, and produced no console error/warn entries.

## Remaining Email Gap

- Continuous mailbox polling/IMAP daemon is not implemented in this PR.
- `/api/email/queue` reads already-ingested mailbox/outbound rows; it does not claim provider daemon ownership.
