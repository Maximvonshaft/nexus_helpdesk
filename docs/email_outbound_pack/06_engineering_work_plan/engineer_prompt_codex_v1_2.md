# Codex / Engineer Execution Prompt v1.2

You are implementing the production-grade Email outbound channel for `Maximvonshaft/nexus_helpdesk`.

## Important correction

v1.1 was not enough for full business-value closure. v1.2 requires backend + frontend + admin configuration + agent usage + delivery/inbound observability.

## Mission

Make Email usable in production by non-engineers:

1. Admin configures Email sending account in backend/admin UI.
2. Admin checks verification/health and sends a test email.
3. Agent selects Email in a ticket and sends a customer reply.
4. Worker sends through SES.
5. Delivery/bounce/complaint events appear in the ticket/admin views.
6. Customer replies link back to the ticket or enter unresolved review.

## Mandatory implementation files

Read and follow:

- `03_scope_contract/e2e_business_value_contract_v1_2.md`
- `03_scope_contract/backend_admin_configuration_contract_v1_2.md`
- `04_solution_architecture/admin_configuration_and_frontend_e2e_design_v1_2.md`
- `04_solution_architecture/email_admin_api_contract_v1_2.md`
- `05_design_ux_content/admin_email_account_configuration_ui_spec_v1_2.md`
- `05_design_ux_content/agent_email_reply_composer_ui_spec_v1_2.md`
- `06_engineering_work_plan/frontend_backend_e2e_file_level_plan_v1_2.md`

## Merge blockers

Do not mark the work complete unless all are implemented:

- Provider-scoped account resolver.
- Email-specific runtime gate.
- Channel-aware worker claim.
- Email account admin APIs.
- Email account admin UI.
- Verification/health/test-send actions.
- Agent Email composer fields.
- SES provider adapter.
- Delivery event webhook with concrete verification.
- Inbound deterministic reply linking.
- Email-specific queue/event observability.
- E2E tests and smoke evidence.

## Explicitly forbidden

- Backend-only implementation.
- Raw AWS credentials stored in DB.
- `OUTBOUND_PROVIDER=ses`.
- Email routed through OpenClaw.
- Subject similarity auto-linking.
- Rollback that dead-letters pending Email.
- UI that shows Email as available when backend says not ready.

## Required final report

Return:

- Branch.
- Commit SHA.
- PR URL.
- Backend changed files.
- Frontend changed files.
- Migration id.
- API contracts implemented.
- Screenshots of admin Email account UI.
- Screenshots of agent Email composer.
- Tests run.
- Smoke evidence.
- Known limitations.
- Rollback procedure.
