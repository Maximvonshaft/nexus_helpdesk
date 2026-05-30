# Email Delivery Receipts Evidence

Date: 2026-05-30
Branch: `codex/email-delivery-receipts`
Base: `codex/email-inbound-ingest-sync`

## Implemented

- Added delivery receipt fields to `TicketOutboundMessage` through migration `20260530_0044_email_delivery_receipts.py`.
- Added `POST /api/tickets/{ticket_id}/email/outbound/{message_id}/delivery-receipt`.
- Receipt ingest is `runtime.manage` gated and still requires ticket visibility.
- Receipt status updates the outbound message final state:
  - `accepted`, `delivered`, `opened` -> `sent`
  - `deferred` -> `failed`
  - `bounced`, `failed`, `rejected`, `complained` -> `dead`
- Receipt ingest writes `TicketEvent(field_name=email.delivery_receipt)` and `AdminAuditLog(email.delivery_receipt.ingested)`.
- Raw receipt payload storage redacts secret/token/password/key-like fields.
- Duplicate provider event ids return the existing receipt state without writing duplicate audit rows.
- `/api/tickets/{ticket_id}/timeline` now returns outbound receipt fields in `outbound_message` items.
- `/email` uses `api.recordEmailDeliveryReceipt` from `webapp/src/lib/api.ts` and renders receipt status/provider/id/timestamp/detail in the Email timeline.

## Local Validation

- `python -m compileall backend\app\models.py backend\app\schemas.py backend\app\services\email_delivery_receipt_service.py backend\app\api\tickets.py backend\app\api\ticket_perf.py backend\app\services\timeline_service.py backend\alembic\versions\20260530_0044_email_delivery_receipts.py backend\tests\test_channel_workbench_backend_contracts.py` passed.
- `node --test webapp\tests\email-workbench-contract.test.mjs` passed: 10 passed.
- `python -m pytest backend\tests\test_channel_workbench_backend_contracts.py -q` passed: 6 passed, 12 warnings.
- `python -m pytest backend\tests\test_channel_workbench_backend_contracts.py backend\tests\test_email_outbound_runtime.py backend\tests\test_migration_drift_gate.py backend\tests\test_rbac_capability_contracts.py -q` passed: 22 passed, 23 warnings.
- `npm test` passed: 84 passed.
- `npm run build` passed. Vite reported the existing `vendor-livekit` chunk size warning.
- `npm run lint` passed with 0 errors and 5 existing hook dependency warnings in unrelated files.
- `git diff --check` passed.
- Browser smoke used the Codex in-app Browser against local Vite. Port 5174 was occupied, so Vite served `http://127.0.0.1:5175/`; opening `/email` redirected to `/login`, rendered a non-blank login page, had no framework error overlay, and produced no console error/warn entries.

## Remaining Email Gaps

- Continuous mailbox polling/IMAP daemon is not implemented in this PR.
- Email queue source remains ticket metadata and markers, not an independent mailbox queue projection.
