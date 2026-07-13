# Nexus OSR Recovery Qualification

## Purpose

This control proves that an exact Nexus revision can be migrated, backed up, restored into a fresh PostgreSQL database and verified without using production data. It supports #532 and the final #533 release decision; it is not a deployment or production-restore authorization.

## Connection authority

Application code uses a SQLAlchemy URL such as `postgresql+psycopg://...`. Native PostgreSQL tools use `POSTGRES_NATIVE_URL` in libpq form: `postgresql://...`.

The operator scripts accept `POSTGRES_NATIVE_URL` directly. For compatibility they may normalize the known SQLAlchemy driver prefixes from `DATABASE_URL`, but they never pass an unmodified `postgresql+psycopg://` URL to `pg_dump`, `pg_restore` or `psql`.

The disposable runner additionally requires `RECOVERY_ADMIN_NATIVE_URL` and `RECOVERY_ALLOW_DATABASE_RECREATE=I_UNDERSTAND`. Before any `DROP DATABASE` or `CREATE DATABASE`, it proves that the admin, source and restore URLs share the same host and port, that application/native URL pairs agree, that the source/restore database names are exactly `nexus_source` and `nexus_restore`, and that the admin URL targets a different database. URI query strings and fragments are rejected because libpq parameters such as `host`, `hostaddr`, `port` or `dbname` could override the visibly validated authority. Ambient `PGHOST`, `PGUSER` or default-database state is never used as destructive authority.

## Backup bundle

`scripts/deploy/backup_postgres.sh` creates one mode-restricted temporary directory containing:

- `database.dump` — custom-format, compressed, owner/privilege-free PostgreSQL archive;
- `backup_manifest.json` — schema version, archive digest and size, hashed source-database identity, exact Alembic head and creation time.

The archive is non-empty and must pass `pg_restore --list`. The manifest and SHA-256 are generated before the temporary directory is atomically renamed to its final bundle path. Publication uses `mv -T`, so a concurrent bundle with the same final name fails instead of being nested inside an existing directory. Failed backups are removed by the exit trap and cannot remain under a plausible final name.

## Rollback states

`scripts/deploy/rollback_release.sh` requires `ROLLBACK_CONFIRM=I_UNDERSTAND` and emits `nexus_operator_rollback_result_v1`.

Possible states are:

- `INSTRUCTIONS_ONLY` — no mutation input was provided;
- `DATABASE_RESTORED` — manifest/checksum/archive validation and transactional `pg_restore` succeeded;
- `IMAGE_RESTARTED` — the requested old image tag was applied through Docker Compose;
- `HEALTH_VERIFIED` — both `/healthz` and `/readyz` returned explicit HTTP 2xx responses after restart.

The result also includes `outcome` and a fixed `failure_stage`. An EXIT trap writes the partial state when an operation fails. For example, if the image restarts but a health check fails or returns a redirect, the result records `IMAGE_RESTARTED`, `outcome=fail`, `failure_stage=HEALTH_VERIFICATION`, and `health_verified=false`.

The script never reports a generic completed state. An image rollback requires `ROLLBACK_HEALTH_URL`; a database restore requires a regular archive/manifest pair and exact checksum match.

## Disposable CI topology

The dedicated gate uses pgvector PostgreSQL 16 and creates:

- `nexus_source` — migrated using the repository's current Alembic chain;
- `nexus_restore` — empty target restored only from the generated backup bundle.

The sequence is:

1. Validate explicit admin/source/restore URL authority before destructive setup.
2. Upgrade source to the single current Alembic head.
3. Downgrade one revision to simulate an interrupted/outdated state.
4. Emit a bounded dry-run plan whose only repair action is `alembic_upgrade_head`.
5. Re-upgrade and verify the exact expected head.
6. Insert one synthetic Market/Team referential marker.
7. Snapshot every public table count, Alembic head, marker count and unvalidated-FK count.
8. Create and validate the real operator backup bundle.
9. Restore through the real rollback script into the disposable target.
10. Compare exact head, complete table/count set, marker and FK state.
11. Measure synthetic RPO and restore RTO.
12. Remove all backup bytes.
13. Scan bounded JSON evidence before upload.

## Evidence

Uploaded JSON may contain only source SHA, Alembic revision, public table names and aggregate counts, backup digest, bounded durations, Boolean integrity results and fixed reason codes. It excludes backup bytes, connection strings and business rows.

The disposable thresholds are:

- RTO: 120 seconds;
- RPO: 60 seconds;
- row-count mismatch: zero;
- unvalidated foreign keys: zero;
- Alembic heads: exactly one matching head.

These are CI qualification thresholds, not production SLOs.

## Failure semantics

The gate fails closed on migration/restore errors, missing/multiple/invalid heads, admin/source/restore cluster mismatch, URI authority overrides, manifest/checksum mismatch, non-2xx health responses, missing marker, table/count mismatch, unvalidated foreign keys, RTO/RPO breach, unsafe evidence or missing evidence. Backup bytes are deleted even when qualification fails.

## Remaining work

Tenant-scoped retention, DSAR, anonymization and legal hold remain blocked on #546. Final #533 acceptance must rerun recovery against the immutable release candidate and realistic staging volume with approved encrypted backup custody, operator ownership and rollback rehearsal.

No merge or green check authorizes a production backup, restore, deletion, deployment, image restart, release tag, Provider action or real outbound.
