# v1.4 Atomic Delivery Pack Changelog

Generated: 2026-05-25

## Reason for v1.4

v1.3 was a strong production implementation pack, but it did not fully meet strict big-tech issue/PR-level minimum granularity. Several tasks still grouped multiple engineering actions, all tasks were marked P0, and task-to-test traceability was not 1:1.

v1.4 upgrades the pack from a design-to-execution pack to an atomic delivery pack.

## What changed

1. Added 92 atomic delivery tasks with:
   - owner role
   - dependencies
   - PR slice
   - exact code-level requirement
   - test command
   - expected evidence
   - rollback impact
   - merge independence
   - priority
2. Added PR slicing plan.
3. Added current-main reference map v1.4.
4. Added task-to-test traceability matrix.
5. Added business value E2E trace v1.4.
6. Added atomic rollback matrix.
7. Added final Codex execution gate v1.4.
8. Added full E2E smoke script skeleton that supports health, account readiness, send, timeline, webhook mock, inbound mock, and rollback invariant checks.
9. Updated the production readiness scorecard for strict atomic execution readiness.
10. Updated validation script to reject non-atomic packs.

## Source of truth

The v1.4 source of truth is:

`06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv`

Older v1.3 boards remain for historical context only.

## Final status

v1.4 is intended to be suitable for big-tech-style execution planning where work can be split into GitHub Issues and PR slices without additional task decomposition.
