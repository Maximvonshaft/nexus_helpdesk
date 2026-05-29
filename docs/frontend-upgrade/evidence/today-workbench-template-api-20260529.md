# Today Workbench Template API Evidence

Date: 2026-05-29
Branch: `codex/today-workbench-template-api`
Base: stacked on `codex/email-mailbox-thread-identity` / PR #315

## Scope

This stacked PR starts landing the v1.7.8 `今日工作台` Role Home as a real production contract instead of a frontend fixture:

- adds `GET /api/lite/today-workbench`
- computes role tasks from visible tickets, operator handoff tasks, SLA due/breach fields, Email candidate markers and runtime recovery state
- computes command-center availability from resolved backend capabilities
- returns SLA priority rows with due/overdue fields for the homepage table
- returns the interaction-state closure matrix required by the template
- makes `/` consume the unified API client endpoint and render the template blocks

## Remaining Work

This does not complete full visual parity for every v1.7.8 page. It closes the backend contract for the first-viewport Role Home blocks and leaves broader 33-screen registry/visual parity migration active.

## Local Validation

Run locally because GitHub Actions are disabled for this repo session:

```text
python -m py_compile backend\app\api\lite.py backend\app\services\today_workbench_service.py backend\tests\test_today_workbench_contract.py
PASS
```

```text
python -m pytest -q backend\tests\test_today_workbench_contract.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_today_workbench_template_api
2 passed, 4 warnings in 10.07s
```

```text
node --test tests\operator-console-contract.test.mjs
23 passed
```

```text
npm test
71 passed
```

```text
npm run build
PASS; existing LiveKit vendor chunk warning remains.
```

```text
npm run lint
PASS; 0 errors, 5 existing react-hooks warnings outside this change.
```

```text
git diff --check
PASS
```

```text
Browser smoke: http://127.0.0.1:5174/ redirects unauthenticated users to /login, renders the login page, and reports 0 console errors.
```
