# Email Composer Inline Upload Evidence

Date: 2026-05-29
Branch: `codex/email-composer-inline-upload`
Base: stacked on `codex/email-outbound-attachments-real-api` / PR #312

## Scope

This stacked PR closes the remaining Email composer attachment UX gap from PR #312:

- `/email` composer exposes a capability-gated multi-file upload control.
- Upload uses the unified frontend API client method `api.uploadTicketAttachment`.
- Uploaded files are stored as external ticket attachments through `/api/tickets/{ticket_id}/attachments`.
- Uploaded attachment ids are immediately selected for draft/save or send payloads.
- Frontend selection/upload respects the backend outbound attachment limit of 10 ids.
- Case detail, timeline, case list, and Email queue queries are invalidated after upload.

## Local Validation

```text
node --test tests\email-workbench-contract.test.mjs
6 passed
```

```text
npm test
68 passed
```

```text
npm run build
PASS
```

```text
npm run lint
0 errors, 5 existing react-hooks warnings
```

```text
git diff --check
PASS
```

## Browser Smoke

`http://127.0.0.1:5174/email` redirected to `http://127.0.0.1:5174/login` when unauthenticated.

Observed:

- title: `登录 · 客服工作台`
- route guard: active
- console errors: `0`

## Product Gap Update

Email outbound attachment support now covers both existing external ticket attachments and inline upload from the Email composer. A later stacked PR adds provider retry controls. Remaining Email gaps are true inbound Email ingestion/sync, mailbox thread identity, and provider delivery receipt ingestion/UI beyond the current provider status fields.
