# Nexus OSR Runtime Evidence Runbook

## Purpose

This runbook governs the read-only M7 runtime evidence contract for Nexus OSR. It detects code, configuration, build and migration drift; evaluates low-cardinality failure budgets; emits bounded metrics; validates synthetic or explicitly authorized staging GET probes; and fails closed on stale, unavailable, contradictory or unredacted evidence.

It does **not** deploy, send customer messages, execute tools, enable providers, mutate production data or retain raw runtime payloads.

## Contract

- Schema: `nexus.osr.runtime_evidence.v1`
- States: `ready`, `degraded`, `not_ready`, `unavailable`
- Mandatory fail-closed states: `not_ready`, `unavailable`
- Artifact limit: 64 KiB
- Probe response limit: 64 KiB
- Default evidence freshness: 900 seconds
- Default artifact retention: 14 days
- Metric labels: `path`, `state`, `kind`
- Forbidden labels: tenant, conversation, ticket, tracking, provider group, arbitrary error text

The configured governed paths are:

1. runtime decision
2. handoff
3. ticket
4. tracking
5. knowledge
6. operations dispatch
7. queue/worker health
8. provider runtime configuration/readiness

## Evidence inputs

The runner accepts four bounded JSON inputs:

- expected runtime identity;
- observed runtime identity;
- aggregate path samples;
- synthetic or read-only staging probe results.

Runtime identity contains only bounded values:

- exact code SHA;
- configuration SHA-256;
- build identifier;
- migration head;
- observation timestamp.

Do not include environment dumps, secrets, raw provider responses, prompts, tool arguments/results, tracking numbers, phone/email, addresses or customer message bodies.

## Synthetic validation

Run the deterministic synthetic evidence gate:

```bash
python backend/scripts/probe_nexus_osr_runtime_evidence.py \
  --config config/observability/nexus_osr_runtime_evidence.json \
  --expected-identity backend/tests/fixtures/nexus_osr_runtime_evidence/expected_identity.json \
  --observed-identity backend/tests/fixtures/nexus_osr_runtime_evidence/observed_identity.json \
  --samples backend/tests/fixtures/nexus_osr_runtime_evidence/samples.json \
  --probe-fixtures backend/tests/fixtures/nexus_osr_runtime_evidence/probes.json \
  --tenant tenant-a \
  --artifact artifacts/nexus-osr-runtime-evidence.json \
  --metrics artifacts/nexus-osr-runtime-evidence.prom \
  --now 2026-07-10T20:00:00Z
```

Synthetic fixtures prove the local contract and failure semantics. They are not production or staging proof.

## Staging read-only verification

Staging verification requires separate authorization, an explicit allow-listed host and a staging-only admin read token. The runner only permits `GET`, sends no request body and rejects mutation-like paths.

```bash
export NEXUS_OSR_STAGING_ADMIN_TOKEN='<staging-read-token>'
python backend/scripts/probe_nexus_osr_runtime_evidence.py \
  --config config/observability/nexus_osr_runtime_evidence.json \
  --expected-identity /safe/input/expected-identity.json \
  --observed-identity /safe/input/observed-identity.json \
  --samples /safe/input/aggregate-samples.json \
  --probe-fixtures /safe/input/approved-probe-fixtures.json \
  --tenant '<tenant-scope>' \
  --staging-base-url 'https://staging.example.invalid' \
  --allow-host 'staging.example.invalid' \
  --artifact /safe/output/runtime-evidence.json \
  --metrics /safe/output/runtime-evidence.prom
```

The token value must never be written to logs, Issues, PR comments or artifacts. Do not point the runner at production. Do not add endpoints that send, execute, publish, create, update or delete.

## Identity drift

Alert: `NexusOSRRuntimeIdentityDrift`

Triage:

1. Compare exact code SHA, configuration SHA-256, build ID and migration head.
2. Verify observation freshness and clock synchronization.
3. Determine whether the mismatch is an expected staged rollout or unauthorized drift.
4. Keep the release state `NO_GO` while mandatory identity evidence is stale, unavailable or mismatched.
5. Roll back configuration/build changes or reconcile the expected identity manifest.
6. Re-run the exact-candidate evidence gate.

Never copy environment values or configuration contents into the artifact. Store only hashes and bounded identifiers.

## Unavailable evidence

Alert: `NexusOSREvidenceUnavailable`

Triage:

1. Confirm the probe is authorized for the requested tenant.
2. Confirm the endpoint is read-only and the host is explicitly allow-listed.
3. Check database/readiness dependencies without issuing writes.
4. Verify that the response is JSON and remains below 64 KiB.
5. Restore the read-only evidence dependency or keep the path failed closed.

Unavailable mandatory evidence cannot be treated as healthy.

## Not-ready evidence

Alert: `NexusOSREvidenceNotReady`

Typical reason codes:

- identity drift;
- stale evidence;
- tenant scope mismatch;
- contradictory evidence;
- redaction failure;
- exhausted failure budget;
- queue backlog above threshold;
- provider runtime not ready.

Resolve the underlying condition and regenerate the bounded artifact. Do not override `not_ready` using a manual green status.

## Failure budget

Alert: `NexusOSRFailureBudgetExhausted`

Each configured path has:

- an accountable owner;
- a window;
- minimum sample size;
- error, unavailable and fail-closed ratios;
- p95 latency threshold;
- optional backlog threshold;
- documented rationale.

Triage:

1. Identify the affected fixed `path` label.
2. Inspect aggregate counts and ratios only.
3. Check whether failures are expected fail-closed decisions or infrastructure errors.
4. Check dependency readiness, queue backlog and recent identity drift.
5. Apply rollback or disable the affected staged capability through its existing governed configuration.
6. Re-run focused and integrated evidence gates.

Do not add identifiers or arbitrary error messages as metric labels.

## Fail-closed spike

Alert: `NexusOSRFailClosedSpike`

A spike may indicate missing facts, provider/readiness failure, policy drift or an upstream outage. Fail-closed behavior is safer than an unsafe answer, but sustained elevation is an operational incident. Reconcile reason codes using bounded aggregates and preserve the governed outbound/tool boundaries.

## Queue backlog

Alert: `NexusOSRQueueBacklogHigh`

Triage queue/worker health without triggering dispatch:

1. Verify worker readiness and last successful heartbeat.
2. Check retry and timeout aggregates.
3. Confirm the backlog metric is tenant-safe and aggregate.
4. Restart or recover workers only through separately authorized operational procedures.
5. Confirm idempotency before resuming processing.

The M7 probe itself never drains or mutates a queue.

## Provider runtime

Alert: `NexusOSRProviderRuntimeNotReady`

This evidence is configuration/readiness only. It must not call a model, send a customer message or execute a tool. Confirm provider feature flags, safe endpoint shape, secret-file presence and runtime isolation through existing bounded readiness surfaces.

## Redaction failure

Alert: `NexusOSRRedactionFailure`

A redaction failure is immediately `not_ready`.

1. Stop publishing the affected artifact or metrics.
2. Delete unsafe transient local output according to incident procedure.
3. Do not paste the leaked value into an Issue or PR.
4. Identify the unsafe key or value class using bounded reason code only.
5. Patch the sanitizer and add a synthetic regression fixture with non-real data.
6. Re-run redaction, cardinality and integration tests.
7. Re-enable evidence publication only after exact-head validation.

## Tenant and permission isolation

Every probe requires:

- an explicit tenant scope;
- existing Admin authentication/permission enforcement at the endpoint;
- a response that identifies the same tenant scope;
- tenant ID hashing in artifacts;
- no tenant ID metric label.

Permission denial is `unavailable`. Tenant mismatch or missing scope is `not_ready`. The probe must not retry against another tenant.

## Alert activation

The rule file is delivered for syntax and contract validation. Paging activation requires separate operational authorization, routing ownership and an approved Alertmanager destination. Start in non-paging validation mode.

## Rollback

No schema migration is introduced.

Rollback steps:

1. Disable the dedicated evidence workflow or collector schedule.
2. Remove the alert rule group from the non-paging evaluator.
3. Revert the runtime evidence module, runner, configuration and runbook together.
4. Preserve already produced bounded audit artifacts for the approved retention period.
5. Do not delete underlying runtime decision audits or operational records.

Rollback does not authorize deployment or production mutation.

## Evidence retention

- Keep bounded artifacts for 14 days by default.
- Retain only hashes, aggregate counts, fixed states and fixed reason codes.
- Never retain raw probe responses after evaluation.
- Shorten retention where tenant or regional policy requires it.
- Incident evidence retention changes require accountable approval.

## Current verification status

The repository gate proves deterministic synthetic/read-only contracts, redaction, tenant/permission failure semantics, metric cardinality and alert syntax. Actual staging access, live traffic, paging activation and production behavior remain unverified until separately authorized and executed against an exact candidate.
