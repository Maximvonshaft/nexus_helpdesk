# Atomic Delivery Board Field Definitions v1.4

| Field | Meaning | Required Quality Bar |
|---|---|---|
| Task ID | Stable atomic work identifier | Unique; use in issue title and PR body |
| Epic | Product/architecture grouping | Must map to business capability |
| PR Slice | Mergeable PR package | Must match `pr_slicing_plan_v1_4.md` |
| Layer | Backend, Frontend, Database, Ops, QA | Used for owner assignment |
| Owner Role | Primary executor | Backend/Frontend/DevOps/Security/QA |
| Depends On | Blocking upstream task IDs | `none` only when truly independent |
| Atomic Change | One work unit | Must be independently understandable |
| Primary Files | Files expected to change | Prevents broad uncontrolled edits |
| Exact Code-Level Requirement | Concrete implementation requirement | Must be testable |
| Acceptance Criteria | What must be true after change | Must not be generic |
| Test Command | Command or manual review required | Must produce evidence |
| Expected Evidence | What reviewer should inspect | Logs, JSON, DB state, screenshot, test output |
| Rollback Impact | How to revert or disable safely | Must mention data impact if any |
| Merge Independence | Whether task can merge independently | `yes` or `no` |
| Priority | P0/P1/P2/P3 | P0 means merge blocker/safety critical |

## Atomicity rule

A task is too large if it contains more than one of:

- a new model/table,
- a new endpoint,
- a new provider integration branch,
- a new frontend page,
- a new event type,
- a new rollout/rollback behavior.

If a task contains more than one of those, split it unless the actions are inseparable in one transaction.
