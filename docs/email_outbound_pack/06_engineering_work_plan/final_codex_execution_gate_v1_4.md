# NexusDesk Email Outbound v1.4 — Final Codex Execution Gate

## Read order

Before coding, Codex/OpenClaw must read these files in order:

1. `README.md`
2. `03_scope_contract/p0_p1_guardrails_addendum.md`
3. `02_problem_definition_evidence/main_code_reference_map_v1_4.csv`
4. `06_engineering_work_plan/pr_slicing_plan_v1_4.md`
5. `06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv`
6. `07_quality_testing/task_to_test_traceability_v1_4.csv`

## Execution source of truth

The execution source of truth is:

`06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv`

Older WBS or v1.3 MG files are retained for context only. Do not use them as the task breakdown.

## Hard blockers

Stop implementation and report if any of these appear:

- A proposal to set `OUTBOUND_PROVIDER=ses`.
- A resolver that can return an Email account for WhatsApp/Telegram/SMS.
- Email rollback that increments retry_count or marks pending Email rows dead.
- Webhook endpoint without signature/HMAC verification and replay protection.
- Inbound linking that auto-links by subject similarity.
- Raw AWS secret persisted in database.
- Email UI that allows sending while capability says `supports_send=false`.
- Existing WebChat local-only behavior changed.

## Merge criteria by PR

Each PR must include:

1. Completed atomic task IDs.
2. Exact test commands run.
3. Evidence output paths or screenshots.
4. Rollback statement.
5. Backward compatibility statement.

## Required final test bundle

Before final merge, run at minimum:

```bash
pytest backend/tests/test_email_runtime_gate.py
pytest backend/tests/test_channel_account_provider_scope.py
pytest backend/tests/test_email_models_migration.py
pytest backend/tests/test_email_admin_api.py
pytest backend/tests/test_email_channel_capabilities.py
pytest backend/tests/test_email_outbound_queueing.py
pytest backend/tests/test_email_dispatch_adapter.py
pytest backend/tests/test_email_provider_ses.py
pytest backend/tests/test_email_delivery_events.py
pytest backend/tests/test_email_webhook_auth.py
pytest backend/tests/test_email_inbound_parser.py
pytest backend/tests/test_email_inbound_linking.py
pytest backend/tests/test_email_observability.py
npm --prefix webapp run typecheck
npm --prefix webapp test
python 15_automation_scripts/validate_pack.py
```

## Final smoke evidence

Before production rollout, collect:

```text
healthz.json
readyz.json
email_accounts_before.json
email_account_readiness.json
email_account_test_send.json
ticket_capabilities_before.json
send_payload.json
send_result.json
queue_summary_before.json
queue_summary_after.json
timeline_after_send.json
delivery_webhook_result.json
bounce_webhook_result.json
suppression_after_bounce.json
inbound_plus_address_result.json
rollback_before.json
rollback_after.json
worker_log_excerpt.txt
```

## Final verdict format

The final implementation report must end with one of:

```text
APPROVED_FOR_STAGING_SMOKE
APPROVED_FOR_LIMITED_PRODUCTION_CANARY
BLOCKED
```

Do not claim production readiness until SES identity/DNS/secret/webhook/inbound smoke evidence exists.
