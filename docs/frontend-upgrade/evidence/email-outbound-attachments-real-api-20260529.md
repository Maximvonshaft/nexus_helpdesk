# Email Outbound Attachments Real API Evidence

Date: 2026-05-29
Branch: `codex/email-outbound-attachments-real-api`

## Scope

This PR closes Email outbound attachment send support for existing external ticket attachments:

- outbound draft/save and send payloads accept `attachment_ids`
- attachment ids are validated against the same ticket and external visibility
- outbound messages persist attachment links through `ticket_outbound_attachments`
- SMTP dispatch builds MIME parts from the stored ticket attachment file paths
- ticket timeline returns outbound attachment ids/count/details for audit readback
- `/email` composer can select existing external ticket attachments when channel capability reports `supports_attachments`

PR #312 scoped the backend attachment-send chain and existing external attachment selection. The stacked inline-upload PR keeps the same backend upload contract, `/api/tickets/{ticket_id}/attachments`, and wires it into the Email composer.

## Local Validation

```text
python -m py_compile backend\app\api\ticket_perf.py backend\app\services\outbound_channel_registry.py backend\tests\test_channel_workbench_backend_contracts.py
PASS
```

```text
python -m pytest -q backend\tests\test_email_outbound_runtime.py backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_email_outbound_attachments_full
13 passed, 15 warnings in 64.30s
```

```text
npm test
67 passed
```

```text
npm run lint
0 errors, 5 existing react-hooks warnings
```

```text
npm run build
PASS
Vite warning only: vendor-livekit chunk remains larger than 500 kB and is still emitted as the lazy LiveKit vendor chunk.
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

The in-app browser text-entry path could not complete an authenticated login because the browser virtual clipboard was unavailable, so this smoke is limited to the protected-route behavior. Frontend route/component behavior is covered by `webapp/tests/email-workbench-contract.test.mjs`.
