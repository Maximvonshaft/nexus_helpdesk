# NexusDesk Branch Protection and Production Gates

## Purpose

This policy separates code-merge gates from production feature enablement gates.

The goal is precision:

- High-risk paths are blocked hard.
- Low-risk changes are not slowed by unrelated heavyweight checks.
- Production feature flags are never enabled only because code merged.

## Required Main Branch Protection

Recommended GitHub settings for `main`:

- Require a pull request before merging.
- Require status checks to pass before merging.
- Require branches to be up to date before merging.
- Dismiss stale approvals when new commits are pushed.
- Require conversation resolution before merging.
- Require linear history.
- Restrict direct pushes to `main`.
- Disable force pushes.
- Disable branch deletion.
- Include administrators.
- Enable merge queue when available.

## Required Checks

The following checks should be required for relevant PRs:

- `backend-ci`
- `postgres-migration`
- `production-readiness`
- `integration-contracts`
- `webapp-build`
- `round-a-smoke`
- `backend-full-regression`
- `provider-runtime-gate`
- `speedaf-contract-gate`

## Gate Responsibilities

| Gate | Responsibility |
|---|---|
| `backend-ci` | Baseline backend compile and targeted backend suites. |
| `postgres-migration` | PostgreSQL Alembic heads, upgrade, downgrade -1, and re-upgrade. |
| `production-readiness` | Production-like environment configuration validation. |
| `integration-contracts` | Integration API contract stability. |
| `webapp-build` | Frontend install, typecheck, build, and size report. |
| `round-a-smoke` | Deterministic backend/frontend smoke and secret sanity check. |
| `backend-full-regression` | Full backend pytest suite, separated from provider-specific gates. |
| `provider-runtime-gate` | Provider runtime, credential safety, provider tests, and route exposure safety. |
| `speedaf-contract-gate` | Speedaf read/write contract, feature-flag safety, PII safety, migration, and focused tests. |

## Path-Aware Expectations

Docs-only PRs may not trigger all checks, but runtime-affecting PRs must trigger the relevant checks:

| Change area | Expected gates |
|---|---|
| Backend runtime | `backend-ci`, `backend-full-regression`, `production-readiness` |
| Alembic migrations | `postgres-migration`, `backend-full-regression` |
| Frontend/webapp | `webapp-build`, `round-a-smoke` |
| Provider and tool runtime | `provider-runtime-gate`, `backend-full-regression` |
| Speedaf code or docs | `speedaf-contract-gate`, `backend-full-regression`, `postgres-migration` when migrations change |
| Integration API | `integration-contracts`, `backend-ci` |

## Speedaf Feature Flag Production Gates

Code merge does not authorize production enablement.

### Read-only tracking

Before enabling Speedaf tracking facts in production:

- `order/query` UAT passes.
- `order/waybillCode/query` UAT passes.
- CallerID format is confirmed.
- Status mapping is validated against Speedaf responses.
- Logs do not leak phone numbers, addresses, full waybills, appCode, secretKey, sign, or tokens.

### Work order create

Before enabling `SPEEDAF_WORK_ORDER_CREATE_ENABLED=true`:

- `WT0103-05` UAT passes.
- Description is confirmed to be at most 200 characters when sent to Speedaf.
- BackgroundJob worker consumes successfully.
- Duplicate requests are deduped.
- TicketEvent and ToolCallLog are visible to operators.
- Failure and retry behavior is observable.

### Address update

Before enabling `SPEEDAF_UPDATE_ADDRESS_ENABLED=true`:

- Operator UI requires confirmation of WhatsApp phone.
- Response copy says confirmation request queued/submitted, not address changed.
- BackgroundJob worker path is verified.
- Duplicate requests are deduped by ticket + waybill + phone.
- Failure status is visible in ticket timeline.

### Cancel order

Before enabling `SPEEDAF_CANCEL_ENABLED=true`:

- Preview calls `order/query`.
- Confirm re-runs `order/query` immediately before cancel.
- Terminal statuses `5`, `730`, and `-2` block cancellation.
- Reason code is limited to `CC01` through `CC05`.
- Confirm token is short-lived and bound to ticket, waybill, caller, reason, and operator.
- DB-backed dedupe is active.
- Backend rate limit is active.
- ToolCallLog and TicketEvent are written.
- Nexus ticket is not auto-closed.
- Two-person operational approval is recorded outside code.

## Non-Goals

- Do not require every historical warning to be zero before merge.
- Do not require heavyweight full regression for docs-only changes.
- Do not allow production feature flags to be enabled by CI alone.
- Do not treat mock smoke success as real Speedaf UAT.
