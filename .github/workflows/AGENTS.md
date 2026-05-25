# .github/workflows/AGENTS.md — CI / GitHub Actions Execution Contract

This contract applies to `.github/workflows/**`. CI workflows are repository control gates. Do not weaken them to make a PR pass. If a gate is wrong, replace it with an equivalent or stronger gate and explain why.

## 1. Mandatory inspection before workflow changes

Before editing a workflow, inspect:

```text
workflow being changed
test files invoked by that workflow
package/requirements files used by that workflow
paths filters and changed-file guards
branch triggers
permissions block
concurrency block
artifact upload behavior
```

Also inspect the code path the workflow protects.

## 2. Existing important workflow classes

| Workflow type | Purpose |
|---|---|
| backend CI | compile backend, run focused backend regression groups, strict production readiness checks |
| backend full regression | run full backend test suite for backend changes |
| webapp build | npm ci, unit tests, typecheck, build, size report, Playwright smoke |
| WebCall PR guard | scope guard and extra quality gate for WebCall/Codex/WebChat voice related files |
| provider/runtime gates | provider runtime, Codex, credential, routing, and safety boundaries |
| migration/readiness gates | production readiness and migration safety |

## 3. Hard rules

- Do not remove tests without adding an equivalent or stronger replacement.
- Do not broaden path filters in a way that makes critical changes skip CI.
- Do not narrow path filters in a way that hides backend/frontend/provider/deployment changes.
- Do not remove `permissions: contents: read` style least-privilege unless the workflow truly needs more.
- Do not remove `timeout-minutes` without justification.
- Do not remove `set -Eeuo pipefail` from shell gates.
- Do not change `pull_request` triggers to push-only for quality gates.
- Do not mark failing security/runtime tests as allowed failure unless explicitly approved.
- Do not downgrade Node/Python versions casually; align with Dockerfile and package/runtime baselines.

## 4. WebCall PR guard rule

`pr-webcall-guard.yml` contains an allowlist for WebCall/Codex/runtime-relevant files and quality checks for selected WebCall files.

When changing WebCall, WebChat voice, Codex runtime, provider runtime, or related deployment files:

```text
Check whether the file is listed in the guard.
If a new guarded file is added, update the allowlist deliberately.
If a guarded file no longer needs checks, explain why and update tests accordingly.
Do not bypass the guard by moving risky code into an unguarded path.
```

## 5. Workflow validation

For workflow-only changes:

```bash
set -Eeuo pipefail
git diff --check
```

Also reason through:

```text
event trigger
path filter
permissions
working-directory
cache key/dependency path
timeout
test command existence
artifact retention
```

For workflow changes that alter test commands, run the affected test commands locally where possible.

## 6. PR evidence

Workflow PRs must report:

```text
workflow file changed
old gate behavior
new gate behavior
why the change is not weakening coverage
affected code paths
commands verified locally
expected CI jobs after PR update
```

## 7. Hard stop examples

Stop and request approval before:

```text
removing backend full regression
removing webapp e2e smoke
removing WebCall guard
removing provider runtime gate
removing migration/readiness validation
adding write permissions to workflows
adding secret exposure to logs/artifacts
changing deployment workflow toward automatic production deploy
```
