# Control-plane governance smoke runbook

This runbook validates the first split replacement for the old PR #6 control-plane patch.

## Scope

Validated scope:

- PR-A: database foundation
- PR-B: persona profiles backend
- PR-C: knowledge base backend
- PR-D: channel-control onboarding backend
- PR-E: frontend governance overview

Out of scope:

- Real OpenClaw account binding
- Real customer channel delivery
- Outbound dispatch behavior changes
- PR #6 merge or cherry-pick
- Frontend editing flows for persona / knowledge / channel onboarding

## Preconditions

- User is authenticated.
- User has at least one of these capabilities:
  - `ai_config.manage`
  - `channel_account.manage`
  - `runtime.manage`
- API is reachable from the webapp.
- Alembic head has been upgraded.

## Smoke matrix

| Area | Check | Expected result |
|---|---|---|
| Navigation | Login as admin/manager | `控制面` nav item is visible |
| Navigation | Login as agent | `控制面` nav item is hidden |
| Persona | Open `/control-plane` with `ai_config.manage` | Persona table loads or shows empty state |
| Knowledge | Open `/control-plane` with `ai_config.manage` | Knowledge table loads or shows empty state |
| Channel tasks | Open `/control-plane` with `channel_account.manage` | Channel task table loads or shows empty state |
| Boundary | Open `/control-plane` as unauthorized user | User is redirected to `/` |
| Safety | Click/refresh page | No OpenClaw dispatch, outbound send, or real customer channel operation is triggered |
| CI | Run backend/frontend workflows | `backend-ci`, `postgres-migration`, `webapp-build`, `round-a-smoke`, `integration-contracts` pass |

## Manual API probes

Use a staging or local environment only.

```bash
curl -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/persona-profiles?limit=5"
curl -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/knowledge-items?limit=5"
curl -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/channel-control/onboarding-tasks?limit=5"
```

Expected:

- `persona-profiles` returns `{ profiles, total }`.
- `knowledge-items` returns `{ items, total }`.
- `onboarding-tasks` returns `{ tasks, total }`.
- Unauthorized users receive `403` from protected API routes.

## Production note

This control-plane page is a read-only governance entry. It is safe to deploy after CI passes because it does not trigger real channel operations. Editing and publishing workflows should be implemented in smaller follow-up PRs.
