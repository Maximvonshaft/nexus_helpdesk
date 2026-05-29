# Email Provider Retry Controls Evidence

Date: 2026-05-29
Branch: `codex/email-provider-retry-controls`
Base: stacked on `codex/email-composer-inline-upload` / PR #313

## Scope

This stacked PR closes the Email provider retry control UI gap:

- ticket timeline outbound rows expose provider status/id, retry counters, failure code/reason, sent time, last attempt time, and next retry time
- `/email` timeline renders provider delivery state for outbound messages
- dead outbound rows expose a single-message requeue button
- requeue stays gated by `runtime.manage` and calls the existing audited API `POST /api/admin/outbound/{message_id}/requeue`
- successful requeue refreshes the current case, timeline, global cases, and Email queue queries

## Local Validation

```text
python -m py_compile backend\app\api\ticket_perf.py backend\app\services\timeline_service.py backend\tests\test_channel_workbench_backend_contracts.py
PASS
```

```text
python -m pytest -q backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_provider_retry_contract
3 passed, 9 warnings
```

```text
node --test tests\email-workbench-contract.test.mjs
7 passed
```

```text
npm test
69 passed
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

Email provider retry controls are now backed by existing runtime recovery APIs and rendered in the Email workbench. Remaining Email gaps are true inbound Email ingestion/sync, mailbox thread identity, and provider delivery receipt ingestion/UI beyond current sent/dead/retry provider status fields.
