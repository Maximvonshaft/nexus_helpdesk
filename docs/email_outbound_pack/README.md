# NexusDesk Email Outbound Production Implementation Pack v1.4

## Final audit status

This v1.4 package supersedes v1.3. It upgrades the Email Outbound implementation pack from a high-quality production construction pack to a strict big-tech-style **Atomic Delivery Pack**.

The core improvement is not a new architecture. The architecture from v1.3 remains valid. The improvement is execution granularity: v1.4 breaks the work into issue/PR-level atomic tasks with owner role, dependency, PR slice, exact code requirement, test command, expected evidence, rollback impact, and merge independence.

## Mandatory source of truth

Use this file as the task execution source of truth:

```text
06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv
```

Do not use older v1.3 WBS/MG files as the execution source of truth. They are retained only for context.

## Mandatory start files

1. `02_problem_definition_evidence/main_code_reference_map_v1_4.csv`
2. `03_scope_contract/p0_p1_guardrails_addendum.md`
3. `06_engineering_work_plan/pr_slicing_plan_v1_4.md`
4. `06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv`
5. `07_quality_testing/task_to_test_traceability_v1_4.csv`
6. `11_release_change_management/atomic_rollback_matrix_v1_4.csv`
7. `06_engineering_work_plan/final_codex_execution_gate_v1_4.md`

## v1.4 additions

- 92 atomic delivery tasks.
- 9 PR slices.
- Provider-scoped account resolver tasks.
- Email-only runtime gate and rollback invariant tasks.
- Backend admin configuration tasks.
- Frontend admin Email account UI tasks.
- Frontend agent Email composer tasks.
- SES provider and delivery webhook tasks.
- Inbound reply deterministic linking tasks.
- Queue/timeline/metrics observability tasks.
- Task-to-test traceability matrix.
- Atomic rollback matrix.
- Full E2E smoke script draft for post-implementation evidence collection.

## Business objective

Enable NexusDesk to support a production-grade Email customer service channel:

```text
Admin configures Email account in backend/admin UI
→ readiness and test-send confirm safety
→ agent sees Email only when sendable
→ agent sends customer Email from ticket
→ worker dispatches through SES
→ provider_message_id is persisted
→ delivery/bounce/complaint events update timeline and suppression
→ customer replies link back to ticket deterministically
→ rollback can stop Email only without burning pending queue
```

## Repository and branch

Repository: `Maximvonshaft/nexus_helpdesk`  
Target branch: `feat/email-outbound-production`  
Recommended PR style: 9 incremental PR slices from `PR-01` to `PR-09`.

## Non-negotiable guardrails

1. Do **not** set `OUTBOUND_PROVIDER=ses`.
2. Email uses its own runtime gate: `OUTBOUND_EMAIL_ENABLED` + `EMAIL_PROVIDER`.
3. Account resolution must be provider-scoped.
4. Email-only rollback must not dead-letter pending Email rows.
5. Webhook endpoints must verify signature/HMAC and prevent replay.
6. Inbound Email must not auto-link by subject similarity.
7. Raw AWS credentials must not be stored in the database.
8. Existing WhatsApp / Telegram / SMS / WebChat behavior must not regress.

## Validation

Run:

```bash
python 15_automation_scripts/validate_pack.py
```

Expected output:

```text
Email outbound implementation pack v1.4 validation passed.
```

## Package date

2026-05-25
