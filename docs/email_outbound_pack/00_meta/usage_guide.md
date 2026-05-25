# Usage Guide

## Who should use this pack

Use this pack for the engineer/agent implementing Email outbound production support in `Maximvonshaft/nexus_helpdesk`.

## Required reading order

1. `README.md`
2. `02_problem_definition_evidence/current_state_findings.md`
3. `03_scope_contract/scope_non_scope.md`
4. `04_solution_architecture/solution_overview.md`
5. `06_engineering_work_plan/execution_brief.md`
6. `06_engineering_work_plan/file_level_change_plan.md`
7. `07_quality_testing/acceptance_criteria.md`
8. `11_release_change_management/rollback_plan.md`

## Execution rule

Do not treat this pack as suggestion text. Treat it as the implementation contract.

## Split strategy

Recommended PR split:

- PR-1: Data model + capability registry + schemas + tests.
- PR-2: SES provider + Email adapter + worker dispatch tests.
- PR-3: SES delivery event webhook + timeline + suppression tests.
- PR-4: Inbound email parser/linking + admin/runbook docs.
- PR-5: Frontend reply box/account capability UI.

If time is constrained, PR-1 and PR-2 may be combined, but do not merge without tests.

## Approval rule

No production enablement until staging live smoke evidence exists.
