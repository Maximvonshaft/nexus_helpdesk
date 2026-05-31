# WebCall Workbench Thread Events Evidence

Date: 2026-05-29
Branch: `codex/webcall-workbench-thread-events`
Base: stacked on `codex/qa-training-template-api` / PR #318

## Scope

This stacked PR hardens the top-level `/webcall` operator workbench so the template AI suggestions and audit panels are backed by real backend thread data:

- returns recent `WebchatAITurn` rows from `/api/webchat/admin/tickets/{ticket_id}/thread`
- returns recent redacted `WebchatEvent` rows from the same thread endpoint
- renders WebChat runtime events in the `/webcall` timeline/audit panel
- gates `/webcall-ai-demo` through the unified `routeAccess` / `RequireCapability` path
- extends the WebCall PR guard to run the real channel workbench backend contract when these thread/event files change
- updates the WebChat/WebCall/Email capability matrix so it reflects the current code rather than the old PR #279 gap

## Security Note

Thread events are sanitized before leaving the backend. Keys containing token, secret, password, authorization, cookie, credential, api_key, or session_key are returned as `[redacted]`.

## Local Validation

Run locally because GitHub Actions are disabled for this repo session:

```text
python -m py_compile backend\app\services\webchat_service.py backend\tests\test_channel_workbench_backend_contracts.py
PASS
```

```text
python -m pytest -q backend\tests\test_channel_workbench_backend_contracts.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_webcall_workbench_thread_events
4 passed, 10 warnings in 16.96s
```

```text
node --test tests\webcall-operator-workbench-contract.test.mjs tests\webcall-ai-demo-contract.test.mjs tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs
37 passed
```

```text
npm test
75 passed
```

```text
npm run build
PASS
Existing warning remains: LiveKit vendor chunk is larger than 500 kB.
```

```text
npm run lint
PASS
Existing warnings remain: 5 react-hooks/exhaustive-deps warnings outside this change.
```

```text
git diff --check
PASS
```

```text
Browser smoke
PASS: unauthenticated /webcall redirects to /login, login screen renders, no Vite/Next/Webpack overlay, no console errors or warnings.
LIMITED: in-app browser screenshot capture timed out; DOM/title/URL/console checks completed.
```
