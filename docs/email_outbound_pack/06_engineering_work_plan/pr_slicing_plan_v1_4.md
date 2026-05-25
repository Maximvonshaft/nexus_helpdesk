# NexusDesk Email Outbound v1.4 — PR Slicing Plan

## Purpose

This document converts the v1.4 atomic task board into mergeable PR slices. It is the execution bridge between the architecture pack and GitHub delivery. The source of truth for atomic tasks is:

`06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv`

## Non-negotiable delivery rule

No PR may merge if it violates any P0 guardrail:

1. Email must not use `OUTBOUND_PROVIDER=ses`.
2. Email account resolution must be provider-scoped.
3. Email-only rollback must not dead-letter pending Email rows.
4. Webhook events must be authenticated and replay-protected.
5. Inbound Email V1 must not auto-link by subject similarity.
6. Existing WhatsApp / Telegram / SMS / WebChat behavior must remain backward compatible.

## PR Slices

| PR Slice | Scope | Atomic Tasks | Merge Gate |
|---|---|---|---|
| PR-01 foundation-runtime-and-db | Settings, migrations, provider-scoped resolver | EMAIL-BE-001..005, EMAIL-DB-006..015, EMAIL-BE-016..018 | DB upgrade/downgrade tests + provider-scope tests |
| PR-02 email-admin-api | Admin Email account APIs, readiness, health, test-send, suppression APIs | EMAIL-BE-019..029 | Admin API tests + auth/permission checks |
| PR-03 capability-and-send-contract | Email capability, send schema, queueing metadata | EMAIL-BE-030..037 | Backward compatibility + capability tests |
| PR-04 email-dispatch-and-ses-provider | Worker claim, Email adapter, renderer, SES provider | EMAIL-BE-038..049 | Dispatch tests + rollback invariant tests |
| PR-05 email-events-and-suppression | Webhook auth, SES event parsing, suppression, timeline serializer | EMAIL-BE-050..058 | Webhook auth + delivery/bounce/complaint tests |
| PR-06 email-inbound-reply | Inbound parser, deterministic linking, unresolved queue/manual link | EMAIL-BE-059..067 | Inbound fixtures + no subject-similarity auto-link tests |
| PR-07 frontend-admin-email-config | Admin Email account UI, readiness, verification, health, test send, suppression UI | EMAIL-FE-068..076 | Typecheck + component tests |
| PR-08 frontend-agent-email-reply | Agent Email composer, payload, missing reasons, timeline UI | EMAIL-FE-077..083 | Typecheck + component tests |
| PR-09 observability-release-smoke | Queue metrics, runbooks, smoke scripts, final quality gates | EMAIL-BE-084..085, EMAIL-OPS-086..090, EMAIL-QA-091..092 | Full smoke evidence + final checklist |

## Preferred merge order

```text
PR-01 → PR-02 → PR-03 → PR-04 → PR-05 → PR-06 → PR-07 → PR-08 → PR-09
```

Frontend PRs may start after PR-02/PR-03 API contracts are stable, but must not merge before the corresponding backend contract tests exist.

## Backward compatibility requirements

- Existing `POST /api/tickets/{id}/outbound/send` calls with `{channel, body}` must remain valid.
- Existing WhatsApp / Telegram / SMS account pages must continue to work.
- Existing WebChat local delivery must remain local-only and must not enter external provider dispatch.
- Email feature flags must default disabled.

## Required PR body evidence

Every PR must include:

```text
Atomic tasks completed:
Test commands run:
Evidence artifacts:
Backward compatibility notes:
Rollback impact:
Known limitations:
```
