# Branch / PR / Issue Workflow

## Branch

`feat/email-outbound-production`

## Commit style

Use small commits:
- `feat(email): add email channel data model`
- `feat(email): add ses provider adapter`
- `feat(email): add delivery event ingestion`
- `feat(email): add inbound reply linking`
- `test(email): cover capability gates`
- `docs(email): add rollout runbook`

## PR title

`feat(email): implement production-grade email outbound channel`

## PR body must include

- Current state.
- What changed.
- Files changed.
- Migration summary.
- Test evidence.
- Manual smoke evidence.
- Risk.
- Rollback plan.
- Follow-up items.

## Reviewers

At minimum:
- backend reviewer,
- frontend reviewer if UI touched,
- DevOps/release owner,
- security/privacy reviewer.
