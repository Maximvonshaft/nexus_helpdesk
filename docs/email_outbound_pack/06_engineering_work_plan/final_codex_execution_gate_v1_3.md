# Final Codex Execution Gate v1.3

Codex/OpenClaw must treat this file as a merge blocker.

## Execution order

1. Read `02_problem_definition_evidence/main_code_reference_map_v1_3.csv`.
2. Read `03_scope_contract/p0_p1_guardrails_addendum.md`.
3. Read `06_engineering_work_plan/minimum_granularity_execution_board_v1_3.csv`.
4. Implement tasks in phase order.
5. Do not start frontend work until backend API contracts are implemented and tested.
6. Do not mark PR ready until every P0 and P1 task has a passing test or documented evidence.

## Hard blockers

The PR is not ready if any condition is true:

- `OUTBOUND_PROVIDER=ses` is introduced or recommended.
- Email account lookup can return a non-Email provider account.
- Non-Email account lookup can return an Email provider account.
- Email-only rollback can increment retry count or dead-letter pending Email rows.
- Email send works without verified account/readiness checks.
- Raw AWS secrets are stored in database or returned to frontend.
- Webhook endpoint accepts unsigned or replayed provider events.
- Inbound email subject similarity auto-links a ticket.
- Agent Email composer says delivered before delivery webhook event.
- Admin Email configuration still requires engineering to modify application code for account metadata.
- Smoke script cannot be executed by copy-paste with env vars.

## Required proof

Attach to PR:

- Test command output.
- Migration upgrade/downgrade evidence.
- API contract smoke evidence.
- Frontend typecheck/build evidence.
- Email-only rollback smoke evidence.
- If live provider is not available, provider mock proof plus explicit live-provider pending checklist.
