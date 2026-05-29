# Email reply templates real API evidence

Date: 2026-05-29
Branch: `codex/email-reply-templates-real-api`

## Product decision

The v1.7.8 Email workbench includes reply templates as part of the composer. This branch implements templates as a real ticket-scoped backend API instead of hard-coded frontend snippets.

## Closed gap

- Added `GET /api/tickets/{ticket_id}/outbound/templates?channel=email`.
- The backend validates ticket visibility and generates templates from ticket/customer context.
- Templates are read-only suggestions and do not create `TicketOutboundMessage` rows or `TicketEvent` audit rows until the operator explicitly saves a draft or sends.
- `/email` loads templates through the shared API client and applies the selected template into the existing subject/body draft form.

## Still out of scope

- Editable organization-wide template library.
- Inbound Email ingestion/sync.
- Delivery receipt UI and provider retry controls.
- Attachment send support.

## Local verification

- `python -m py_compile backend\app\schemas.py backend\app\api\tickets.py backend\app\services\ticket_service.py backend\tests\test_channel_workbench_backend_contracts.py`
- `python -m pytest -q backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_reply_templates`
- `node --test tests\email-workbench-contract.test.mjs`
- `python -m pytest -q backend\tests\test_channel_workbench_backend_contracts.py backend\tests\test_ticket_lightweight_contract.py backend\tests\test_ticket_timeline_pagination.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_reply_templates_full`
- `npm run typecheck`
- `npm run lint` (0 errors, existing unrelated warnings only)
- `npm test`
- `npm run build` (existing `vendor-livekit` chunk-size warning)
- Browser smoke: `http://127.0.0.1:5174/email` redirects unauthenticated users to `/login`, with no Vite overlay or severe console errors.
