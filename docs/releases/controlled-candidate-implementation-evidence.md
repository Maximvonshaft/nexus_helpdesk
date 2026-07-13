# Controlled Candidate Implementation Evidence

- Work Item: #714
- Parent release qualification: #707
- Server execution: #709
- Independent acceptance: #710
- Starting main: `1d48ee935c55949837f87bee361718b725586918`
- Alembic Head: `20260713_0059`
- Migration authored: none
- Deployment performed: none

## Root cause

The pre-existing RC workflow and release-image assurance workflow independently rebuilt the same source, so source identity matched while binary image identity did not. This implementation retains the merged RC orchestration as the single build authority, then scans, licenses, publishes, pulls back and attests that same binary.

## Review remediation

Exact-head review additionally required:

- image-embedded build time and application version to survive GHCR push/pullback and be bound into the final manifest;
- Swiss preflight to verify that metadata, PostgreSQL port `5432`, deployment secrets, local backup controls and host mounts;
- non-main manual workflow dispatches to fail explicitly rather than succeeding through skipped jobs.

## Local contract evidence

The current controlled release slice executes:

```text
python -m unittest -v \
  scripts.release.tests.test_build_controlled_candidate_manifest \
  scripts.release.tests.test_controlled_candidate_workflow_contract \
  scripts.deploy.tests.test_validate_controlled_server_preflight

19 focused tests
YAML parse
bash -n for all controlled release helpers
```

GitHub workflow evidence is valid only for the exact PR Head on which every applicable check completes successfully. Earlier-head evidence is stale.

## Safety disposition

The first Swiss cutover profile forces Provider, AI auto-reply, real outbound, WhatsApp, SpeedAF writes, Operations Dispatch and WebCall execution off. The existing server's enabled external-action settings are not inherited as authority.

`PRODUCTION_READY=false`, `FULL_OSR_AUTOMATION=NO_GO`, `ISSUE_533_GO=false` and `deployment_performed=false` remain mandatory manifest fields.
