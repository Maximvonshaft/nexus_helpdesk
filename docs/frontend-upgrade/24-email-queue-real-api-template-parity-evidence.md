# Email queue real API template parity evidence

Date: 2026-05-29
Branch: `codex/email-queue-real-api`

## Product decision

The v1.7.8 template treats Email as its own operator queue. The `/email` workbench now uses the backend lite cases queue with `source_channel=email` instead of loading the generic ticket queue and filtering it in the browser.

## Closed gap

- Backend `/api/lite/cases` accepts `source_channel` and validates it against the real `SourceChannel` enum.
- `/email` calls `api.cases({ source_channel: 'email' })`.
- The Email queue no longer falls back to generic ticket rows when no email-like candidate is found.
- Queue metrics and badges now describe the backend Email queue truth, not frontend token heuristics.

## Still out of scope

- Real inbound Email ingestion/sync.
- Attachment send support.
- Provider delivery receipt polling beyond existing outbound message provider status.
- Mailbox thread identity beyond existing ticket summary fields.

## Local verification

- `node --test tests\email-workbench-contract.test.mjs`
- `python -m pytest -q backend\tests\test_lite_cases_pagination.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_queue_real_api`
- `python -m pytest -q backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_queue_api_contract`
- `python -m pytest -q backend\tests\test_lite_cases_pagination.py backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_queue_combined`
- `python -m py_compile backend\app\api\lite.py backend\app\services\lite_pagination.py backend\tests\test_lite_cases_pagination.py backend\tests\test_channel_workbench_backend_contracts.py`
- `npm run typecheck`
- `npm run lint` (0 errors, existing unrelated warnings only)
- `npm test`
- `npm run build` (existing `vendor-livekit` chunk-size warning)
- Browser smoke: `http://127.0.0.1:5174/email` redirects unauthenticated users to `/login`, with no Vite overlay or severe console errors.
