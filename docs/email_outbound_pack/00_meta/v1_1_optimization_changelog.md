# v1.1 Optimization Changelog

## Why this version exists

v1 correctly defined Email as a first-class NexusDesk outbound adapter, but a final audit against current `main` found several merge-blocking risks that could prevent a 100% production guarantee.

v1.1 adds mandatory guardrails to close those risks.

## Added files

- `02_problem_definition_evidence/main_fact_based_audit_v1_1.md`
- `03_scope_contract/p0_p1_guardrails_addendum.md`
- `04_solution_architecture/email_runtime_gate_and_account_resolver_design.md`

## Updated files

- `README.md`
- `06_engineering_work_plan/engineer_prompt_codex.md`
- `06_engineering_work_plan/file_level_change_plan.md`
- `07_quality_testing/acceptance_criteria.md`
- `07_quality_testing/test_matrix.csv`
- `09_devops_ci_cd/environment_config_matrix.csv`
- `11_release_change_management/rollback_plan.md`
- `15_automation_scripts/validate_pack.py`

## Main changes

1. Added provider-scoped ChannelAccount resolver requirement.
2. Separated Email provider gate from global `OUTBOUND_PROVIDER`.
3. Added channel-aware worker claim semantics.
4. Required Email rollback to pause, not dead-letter, pending rows.
5. Required concrete webhook authentication.
6. Forbid subject-similarity auto-link in V1.
7. Added Email-specific queue observability.
8. Expanded test matrix with P0/P1 guardrail tests.

## Final status

```text
v1.1 is the implementation baseline.
v1 is superseded.
```
