# Email Mailbox Polling Daemon Evidence

Date: 2026-05-30
Branch: `codex/email-mailbox-polling-daemon`

## Implemented

- Added IMAP inbound configuration to `OutboundEmailAccount`, with Alembic migration, masked API reads, encrypted secret storage, and redacted IMAP password rotation audit.
- Added `email.mailbox_sync` background jobs and daemon-style due-account enqueue before background worker dispatch.
- Added IMAP polling service that selects configured mailboxes, fetches unseen UIDs, parses RFC822 messages, resolves the ticket from `nexusdesk-ticket-{id}` mailbox references or sender fallback, and writes inbound rows through the same system ingest path used by manual inbound sync.
- Added `/api/email/mailbox-sync/status` and `/api/email/mailbox-sync/enqueue`, both `runtime.manage` gated.
- Wired `/email` to show mailbox daemon status and manually enqueue sync jobs through the shared API client.
- Wired `/outbound-email` to manage inbound IMAP host, port, username, mailbox, security mode, enablement, and password rotation.

## Local Validation

- `python -m compileall backend\app\models.py backend\app\settings.py backend\app\schemas.py backend\app\api\admin_outbound_email.py backend\app\api\email.py backend\app\services\outbound_email_account_service.py backend\app\services\email_inbound_service.py backend\app\services\email_mailbox_polling_service.py backend\app\services\background_jobs.py backend\app\services\background_job_transaction_boundary.py backend\alembic\versions\20260530_0045_email_mailbox_polling.py`
- `python -m pytest backend\tests\test_admin_outbound_email_api.py backend\tests\test_email_mailbox_polling_service.py -q`
- `node --test tests\email-workbench-contract.test.mjs tests\outbound-email-contract.test.mjs` from `webapp/`
- `npm test` from `webapp/`: 85 passed.
- `npm run build` from `webapp/`: passed; Vite emitted the existing large `vendor-livekit` chunk warning.
- `npm run lint` from `webapp/`: passed with 5 existing warnings and 0 errors.
- `python -m pytest backend\tests\test_channel_workbench_backend_contracts.py backend\tests\test_email_outbound_runtime.py backend\tests\test_email_mailbox_polling_service.py backend\tests\test_admin_outbound_email_api.py backend\tests\test_migration_drift_gate.py backend\tests\test_rbac_capability_contracts.py -q`: 33 passed.
- `git diff --check`: passed.
- Browser smoke against local FastAPI serving `frontend_dist` with a seeded SQLite dev database: login `admin/demo123`, open `/email`, verify Email workbench, queue, composer, and `Mailbox Polling / IMAP Daemon` card render; browser console errors: `[]`.

## Contract Boundaries

- The backend contract test uses a fake IMAP client so it can prove parser, worker, API, timeline and audit behavior locally without real provider credentials.
- Real mailbox production enablement still requires deployment-level IMAP credentials, allowlisting, monitoring and provider-specific rate/backoff tuning.
