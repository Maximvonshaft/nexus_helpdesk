# Nexus OSR PostgreSQL resilience qualification

This gate is a bounded release-qualification harness. It is not a production load test and it does not authorize a production release.

## Qualified contracts

| Scenario | Workload | Pass threshold |
| --- | --- | --- |
| Concurrent queue claim | 24 synthetic `BackgroundJob` rows, 4 independent PostgreSQL sessions, 6 claims per worker | every worker receives 6 rows; 24/24 rows are claimed once; no duplicate row ID; all 4 worker identities are represented |
| Active dedupe enqueue | 8 concurrent transactions enqueue one active dedupe key | exactly 1 active durable row remains; all callers resolve the same row ID |
| Expired worker lease recovery | 1 synthetic `processing` row with a lock older than `JOB_LOCK_SECONDS` | the same row is reclaimed by the recovery worker and exactly 1 durable row remains |

The tests execute the production `enqueue_background_job` and `claim_pending_jobs` boundaries against a disposable PostgreSQL 16 database with pgvector, after the repository proves exactly one Alembic Head and upgrades to it.

## Fail-closed evidence contract

The workflow checks out the immutable pull-request Head and records that SHA in `report.json`. A report can be `pass` only when:

1. the source identity is a valid exact 40-character Git SHA;
2. the three required normalized scenario identities each occur exactly once in JUnit evidence;
3. declared JUnit test count, actual testcase count and required scenario count are all exactly three;
4. no renamed, unrelated, duplicated or additional testcase is present;
5. the test process exits successfully;
6. failures, errors and skipped tests are all zero;
7. the report remains bounded to aggregate counts and is smaller than 8 KiB;
8. the bounded artifact scanner reports no prohibited material.

A green aggregate count is not sufficient. Missing, renamed, duplicated, unrelated replacement and unrelated additional testcases all fail closed. Pytest parameter suffixes are normalized only for internal identity counting.

The uploaded artifact contains only:

- `report.json` — aggregate status, counts, exact-source identity and aggregate required/missing/duplicated/unexpected coverage;
- `artifact-scan.json` — bounded scanner result.

Raw JUnit output is deleted before upload. Testcase names, parameter values, payloads, customer identifiers and Provider material are not emitted in the aggregate report.

## Safety boundary

- GitHub Actions creates the disposable PostgreSQL service.
- Fixtures use synthetic queue names, payloads, worker IDs and dedupe keys.
- Synthetic rows are deleted after every scenario.
- No Provider call, external dispatch, customer-visible message, credential, deployment, release tag or production-data mutation is permitted.
- The workflow uses repository dependencies and existing migrations; it creates no migration and changes no production schema.

## Reproduction

With an isolated PostgreSQL+pgvector database and the repository test environment configured:

```bash
cd backend
test "$(alembic heads | sed '/^[[:space:]]*$/d' | wc -l)" -eq 1
alembic upgrade head
cd ..
pytest -q backend/tests/resilience/test_resilience_report.py
pytest -q backend/tests/resilience/test_postgres_worker_recovery.py \
  --junitxml=/tmp/osr-resilience-junit.xml
python backend/scripts/resilience/build_resilience_report.py \
  --junit /tmp/osr-resilience-junit.xml \
  --pytest-exit-code 0 \
  --source-sha "$(git rev-parse HEAD)" \
  --output /tmp/osr-resilience-report.json
```

## Interpretation and remaining work

A passing gate proves only the listed persistence and worker-recovery contracts on the tested commit. It does **not** prove:

- Operations Dispatch Provider acknowledgement or reconciliation;
- dependency timeout, retry or circuit-breaker behavior;
- sustained production capacity, rate limits or saturation thresholds;
- backup restore, retention or disaster recovery;
- final release eligibility.

Those remain separate #531 slices coordinated with #532, #549, #567 and the exact #533 release candidate. The affected scenarios must be rerun on that immutable candidate before final M12 acceptance.
