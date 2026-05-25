# Deprecated by v1.2

This v1.1 prompt is superseded by `06_engineering_work_plan/engineer_prompt_codex_v1_2.md`. Use v1.2 for implementation.

# Codex / Engineer Execution Prompt v1.1

You are implementing a production-grade Email outbound channel in `Maximvonshaft/nexus_helpdesk`.

## Mandatory reading

Read these files first:

1. `README.md`
2. `02_problem_definition_evidence/current_state_findings.md`
3. `03_scope_contract/scope_non_scope.md`
4. `04_solution_architecture/solution_overview.md`
5. `06_engineering_work_plan/file_level_change_plan.md`
6. `07_quality_testing/acceptance_criteria.md`
7. `08_security_privacy_compliance/security_review_checklist.md`
8. `11_release_change_management/rollback_plan.md`
9. `02_problem_definition_evidence/main_fact_based_audit_v1_1.md`
10. `03_scope_contract/p0_p1_guardrails_addendum.md`
11. `04_solution_architecture/email_runtime_gate_and_account_resolver_design.md`

## Mission

Implement Email as a first-class customer support outbound channel.

Current state:
- `SourceChannel.email` exists.
- Email is blocked as `experimental_not_ready`.
- Current send endpoint is gated by capability registry.
- Existing outbox/worker/retry/dead-letter can be reused.
- WhatsApp adapter is the pattern to follow.

Target state:
- Email is conditionally sendable only when fully configured.
- SES provider sends email through worker.
- Provider message id is persisted.
- Delivery/bounce/complaint events are stored.
- Customer replies can link back to tickets.
- Tests prove all gates and failure paths.

## Non-negotiable rules

1. Do not send from request thread; use outbox/worker.
2. Do not store provider secret values in DB.
3. Do not log secrets.
4. Do not make Email visible/sendable unless capability gates are satisfied.
5. Do not alter WhatsApp/Telegram/SMS/WebChat behavior except shared safe registry logic.
6. Do not implement bulk/marketing sending.
7. Keep defaults fail-closed:
   - `OUTBOUND_EMAIL_ENABLED=false`
   - `EMAIL_PROVIDER=disabled`
8. Add tests for success, failure, retry, dead, capability, webhook, inbound parser, and security.
9. Use minimal invasive design.
10. Produce a final report.



## v1.1 mandatory implementation guardrails

Treat these as merge blockers:

### G-001 Provider-scoped account resolution

Implement provider-scoped account resolution before adding any Email account rows. Existing WhatsApp/Telegram/SMS must never resolve `provider=email`.

### G-002 Do not overload `OUTBOUND_PROVIDER`

Keep `OUTBOUND_PROVIDER=openclaw` for WhatsApp/Telegram/SMS. Email must use `OUTBOUND_EMAIL_ENABLED` and `EMAIL_PROVIDER=ses`.

Forbidden:

```text
OUTBOUND_PROVIDER=ses
ALLOWED_OUTBOUND_PROVIDERS={"openclaw","ses"}
```

### G-003 Worker claim must be channel-aware

Do not let `claim_pending_messages(...)` claim Email rows when Email is disabled.

Required:

```text
ENABLE_OUTBOUND_DISPATCH=true + OUTBOUND_PROVIDER=openclaw + OUTBOUND_EMAIL_ENABLED=false
```

must still allow non-email external dispatch while keeping pending Email rows untouched.

### G-004 Email rollback must not dead-letter pending rows

Email-only rollback must pause or keep pending Email rows. Do not increment `retry_count`; do not mark dead.

### G-005 No inbound auto-link by subject similarity

Subject similarity may create manual review/unresolved evidence only. It cannot auto-link.

### G-006 Webhook verification is mandatory

Implement concrete verification: SNS signature verification or HMAC timestamp anti-replay. Unsigned webhooks must be rejected.

### G-007 Final report must include proof

Final report must include test evidence for all P0 guardrails, not only happy-path send.

## Required output from engineer

- Branch name.
- Commit SHA.
- PR URL.
- Changed files.
- Migrations added.
- Tests run and results.
- Manual smoke steps.
- Screenshots if UI changed.
- Known risks.
- Rollback plan.
