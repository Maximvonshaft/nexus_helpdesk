# QA Training Template API Evidence

Date: 2026-05-29
Branch: `codex/qa-training-template-api`
Base: stacked on `codex/control-tower-template-api` / PR #317

## Scope

This stacked PR lands the v1.7.8 `QA / Training / Knowledge Gap Loop` as a real lead/manager-facing read model:

- adds `qa.manage` capability to backend and frontend RBAC manifests
- adds `GET /api/lite/qa-training`
- computes QA samples from WebCall voice sessions, WebChat AI/safety review state, Email outbound state and ticket AI quality fields
- exposes scorecard rows, coaching/training tasks, knowledge-gap candidates, loop steps and template block closure state
- adds `/qa-training` to the authenticated router, AppShell navigation and CommandPalette

## Remaining Work

This PR does not add write endpoints for scoring submission, agent appeal, or automatic knowledge-draft creation. The route marks agent appeal as `not_implemented` instead of pretending the loop is fully writable.

## Local Validation

Run locally because GitHub Actions are disabled for this repo session:

```text
python -m py_compile backend\app\api\lite.py backend\app\services\permissions.py backend\app\services\qa_training_service.py backend\tests\test_qa_training_contract.py
PASS
```

```text
python -m pytest -q backend\tests\test_qa_training_contract.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_qa_training_template_api
2 passed, 4 warnings in 9.42s
```

```text
node --test tests\operator-console-contract.test.mjs tests\route-nav-consistency.test.mjs tests\novice-ux-regression.test.mjs
40 passed
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
PASS: unauthenticated /qa-training redirects to /login, login screen renders, no console errors.
LIMITED: authenticated browser input could not be completed because the in-app browser runtime reports that its virtual clipboard is not installed. Authenticated API behavior is covered by backend contract tests above.
```
