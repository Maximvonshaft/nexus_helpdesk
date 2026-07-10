# Nexus OSR Runtime Evidence Runbook

This runbook operates the M7 runtime evidence contract introduced for #523.

## Scope

The runtime evidence gate is read-only. It produces bounded JSON for:

- code/config/runtime identity drift;
- failure budgets for governed OSR paths;
- alert definitions and reason codes;
- synthetic staging-verification probes;
- redaction and evidence freshness failures.

It does not deploy, page, send customer messages, execute tools, enable providers, mutate production data, or collect raw production payloads.

## Local validation

```bash
PYTHONPATH=backend python backend/scripts/probe_nexus_osr_runtime_evidence.py \
  --expected-sha "$(git rev-parse HEAD)" \
  --output runtime-evidence-report.json
```

Expected states are `ready`, `degraded`, `not_ready`, and `unavailable`.

## Alert reason codes

| Reason code | Meaning | First response |
|---|---|---|
| `runtime_identity_drift` | code, config, migration head, or image identity differs from the expected candidate | hold release; compare exact candidate, config fingerprint, and migration head |
| `audit_unavailable` | governed runtime audit evidence cannot be read | fail closed; check audit persistence and permissions |
| `stale_evidence` | evidence is older than the declared failure budget | rerun synthetic/read-only probe; do not reuse old artifacts |
| `redaction_failed` | unsafe prompt, provider, tool, tracking, contact, credential, or payload material is detected | block release; remove unsafe artifact and fix sanitizer boundary |
| `queue_backlog` | operator/dispatch queue backlog exceeds the configured threshold | inspect queue health and worker status before release |

## Triage policy

1. Treat `not_ready` and `unavailable` as release blockers for this evidence gate.
2. Treat `degraded` as conditional: the PR must explain the degraded path, owner, expiry, and rollback.
3. Never paste raw runtime payloads into Issues, PRs, artifacts, screenshots, or logs.
4. Use reason codes and bounded hashes rather than customer identifiers.

## Rollback

Rollback is a normal revert of:

- `backend/app/services/nexus_osr/runtime_evidence.py`
- `backend/scripts/probe_nexus_osr_runtime_evidence.py`
- `backend/tests/test_nexus_osr_runtime_evidence.py`
- `.github/workflows/osr-runtime-evidence.yml`
- this runbook

No database migration, production data repair, provider disablement, or deployment rollback is required.
