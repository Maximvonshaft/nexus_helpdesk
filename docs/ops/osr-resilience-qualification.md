# Nexus OSR resilience qualification

This gate is a release-qualification harness, not a production load test.

## Current scenarios

1. Multiple PostgreSQL workers claim one bounded job set concurrently with `FOR UPDATE SKIP LOCKED`; every worker receives its full bounded batch and every job is claimed exactly once.
2. Multiple transactions enqueue the same active dedupe key; exactly one active durable row must remain and every caller must resolve that row.
3. A processing job whose lock has expired after a synthetic worker crash must be reclaimed by a new worker without creating another job.

## Fail-closed evidence contract

- The disposable database must upgrade through exactly one current Alembic Head.
- A green test count alone is insufficient: the JUnit evidence must contain all three required named scenarios.
- The uploaded report exposes only aggregate expected/observed/missing scenario counts, never test names or payloads.
- Any failed, errored, skipped, missing-scenario or source-unbound run is reported as failed.

## Safety boundary

- Runs only against the disposable PostgreSQL service created by GitHub Actions.
- Uses synthetic queue names, payloads, worker IDs and dedupe keys.
- Performs no Provider call, outbound dispatch or deployment.
- Deletes synthetic rows after every scenario.
- Uploads only a bounded aggregate report and its artifact-scan result; raw JUnit output is removed before upload.

## Evidence status

Passing this gate proves only the listed persistence and worker-recovery contracts on the tested candidate. It does not prove Operations Dispatch Provider acknowledgement, dependency outage handling, production capacity, backup restore or final release eligibility. Those remain separate qualification slices under #531, #532, #567 and #533.
