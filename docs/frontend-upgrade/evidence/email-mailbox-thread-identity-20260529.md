# Email Mailbox Thread Identity Evidence

Date: 2026-05-29
Branch: `codex/email-mailbox-thread-identity`
Base: stacked on `codex/email-provider-retry-controls` / PR #314

## Scope

This stacked PR closes the outbound Email mailbox identity gap:

- `TicketOutboundMessage` stores `mailbox_thread_id`, `mailbox_message_id`, and `mailbox_references`
- Email draft save persists ticket-scoped thread identity and audit payload evidence
- Email send persists ticket-scoped thread/message identity and audit payload evidence
- SMTP dispatch uses the stored identity for `Message-ID`, `In-Reply-To`, and `References`
- `/email` timeline renders mailbox thread/message/references inside the provider delivery status block
- timeline payloads expose the same mailbox identity values for contract verification

## Remaining Email Gaps

This does not implement inbound Email ingestion/sync, inbound mailbox thread merge, or provider delivery receipt ingestion. Those remain backend/provider work beyond the outbound ticket reply path.

## Local Validation

Run locally because GitHub Actions are disabled for this repo session:

```text
python -m py_compile backend\alembic\versions\20260529_0040_email_mailbox_identity.py backend\app\models.py backend\app\services\email_mailbox_identity.py backend\app\services\background_jobs.py backend\app\services\message_dispatch.py backend\app\services\outbound_adapters\email.py backend\app\services\ticket_service.py backend\app\api\tickets.py backend\app\api\ticket_perf.py backend\app\services\timeline_service.py backend\tests\test_channel_workbench_backend_contracts.py backend\tests\test_email_outbound_runtime.py
PASS
```

```text
python -m pytest -q backend\tests\test_email_outbound_runtime.py backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_mailbox_identity
14 passed, 20 warnings
```

```text
node --test tests\email-workbench-contract.test.mjs
8 passed
```

```text
npm test
70 passed
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
