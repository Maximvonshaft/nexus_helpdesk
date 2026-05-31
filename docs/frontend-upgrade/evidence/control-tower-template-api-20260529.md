# Control Tower Template API Evidence

Date: 2026-05-29
Branch: `codex/control-tower-template-api`
Base: stacked on `codex/today-workbench-template-api` / PR #316

## Scope

This stacked PR lands the v1.7.8 `Control Tower / Governance Console` as a real manager-facing contract instead of a template-only operations page:

- adds `GET /api/lite/control-tower`
- gates the endpoint and route behind management/governance capabilities
- computes KPI, SLA, handoff, WebCall, bulletin, Email/channel, runtime, RBAC and AI-governance facts from current backend tables
- adds `/control-tower` to the authenticated router, AppShell navigation and CommandPalette
- renders the v1.7.8 blocks for KPI/tower, manager action queue, team workload, channel health, bulletin impact, governance lanes and template closure status

## Remaining Work

This does not complete all 33 visible template screens. It closes the next Operations template block and keeps QA/Training Loop, broader visual parity polish and remaining template registry migration active.

## Follow-up Closure

Follow-up branch `codex/control-tower-governance-actions` adds the governance action write path:

- `POST /api/lite/control-tower/actions`
- persists Control Tower actions as `operator_tasks.task_type=control_tower_action`
- writes admin audit evidence through `AdminAuditLog(action=control_tower.action.submitted)`
- updates `/api/lite/control-tower` so active action task status is reflected back into manager actions
- marks Provider / Channel Ops and Speedaf Wizard template blocks as `implemented` through the governance action contract plus their existing capability-gated execution pages

## Local Validation

Run locally because GitHub Actions are disabled for this repo session:

```text
python -m py_compile backend\app\api\lite.py backend\app\services\control_tower_service.py backend\tests\test_control_tower_contract.py
PASS
```

```text
python -m pytest -q backend\tests\test_control_tower_contract.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_control_tower_template_api
2 passed, 4 warnings in 10.03s
```

```text
npm test
73 passed
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
Browser smoke: http://127.0.0.1:5174/control-tower redirects unauthenticated users to /login, renders the login page, and reports 0 console errors.
```
