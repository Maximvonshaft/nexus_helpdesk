# Knowledge Studio Template API Evidence

Date: 2026-05-29
Branch: `codex/knowledge-studio-template-api`
Base: stacked on `codex/webcall-workbench-thread-events` / PR #319

## Scope

This stacked PR lands the v1.7.8 `Knowledge Studio / 知识库配置与发布` as a real AI Ops read model:

- adds `GET /api/lite/knowledge-studio`
- computes asset library readiness from `knowledge_items`
- computes retrieval evidence from `knowledge_chunks` and validates with `POST /api/knowledge-items/retrieve-test`
- exposes derived same-scope conflict rows from real question/alias fields
- shows release lifecycle counts from draft, upload/parse, publish, version and rollback tables
- adds `/knowledge-studio` to router, AppShell navigation and CommandPalette behind unified routeAccess

## Follow-up Closure

Follow-up branch `codex/knowledge-studio-conflict-golden` adds the dedicated conflict and golden-test commands:

- `POST /api/knowledge-items/conflict-check`
- `POST /api/knowledge-items/golden-test`

After that follow-up, `/api/lite/knowledge-studio` marks `dedicated_conflict_check_endpoint` and `dedicated_golden_test_endpoint` as `implemented` instead of `not_implemented`.

## Local Validation

Run locally because GitHub Actions are disabled for this repo session:

```text
python -m py_compile backend\app\api\lite.py backend\app\services\knowledge_studio_service.py backend\tests\test_knowledge_studio_contract.py
PASS
```

```text
python -m pytest -q backend\tests\test_knowledge_studio_contract.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_knowledge_studio_template_api
2 passed, 4 warnings in 9.28s
```

```text
node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs
34 passed
```

```text
npm test
77 passed
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
Target: unauthenticated /knowledge-studio -> /login guard, no framework overlay, no console errors.
PASS: route redirected to http://127.0.0.1:5174/login, login screen rendered, password field focus interaction worked, console error/warn log was empty.
LIMITED: screenshot capture timed out in the in-app browser CDP path; DOM snapshot and console checks were collected.
```
