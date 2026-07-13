# Nexus OSR Recovery Qualification

## Purpose

This control proves that an exact Nexus revision can be migrated, backed up, restored into a fresh PostgreSQL database and verified without using production data. It supports #532 and the final #533 release decision; it is not a deployment or production-restore authorization.

## Connection authority

Application code uses a SQLAlchemy URL such as `postgresql+psycopg://...`. Native PostgreSQL tools use `POSTGRES_NATIVE_URL` in libpq form: `postgresql://...`.

The operator scripts accept `POSTGRES_NATIVE_URL` directly. For compatibility they may normalize known SQLAlchemy driver prefixes from `DATABASE_URL`, but they never pass an unmodified `postgresql+psycopg://` URL to `pg_dump`, `pg_restore` or `psql`. Before any native client call, both scripts require an explicit user, host and database path and reject URI query strings or fragments, so neither `PGUSER` nor libpq URI parameters can replace the visibly reviewed identity or target.

The disposable runner additionally requires `RECOVERY_ADMIN_NATIVE_URL` and `RECOVERY_ALLOW_DATABASE_RECREATE=I_UNDERSTAND`. Before any destructive statement, it proves that:

- application/native URL pairs have the same explicit identity;
- source, restore and admin use the exact distinct roles `nexus_recovery_source`, `nexus_recovery_restore` and `nexus_recovery_admin`;
- all URLs share the same host and port;
- source/restore database names are exactly `nexus_source` and `nexus_restore`;
- the admin URL targets a third database;
- the source/restore roles exist, can log in and have no SUPERUSER, CREATEDB or CREATEROLE privilege;
- the admin role has CREATEDB authority.

The admin creates each disposable database with its corresponding non-admin role as owner, and the runner verifies those owners before Alembic starts. Ambient `PGHOST`, `PGUSER`, `PGPASSWORD` or default-database state is not destructive authority.

## Backup bundle

`scripts/deploy/backup_postgres.sh` creates one mode-restricted temporary directory containing:

- `database.dump` — custom-format, compressed, owner/privilege-free PostgreSQL archive;
- `backup_manifest.json` — schema version, archive digest and size, hashed source-database identity, exact Alembic head and creation time.

The archive is non-empty and must pass `pg_restore --list`. The manifest and SHA-256 are generated before the temporary directory is atomically renamed to its final bundle path. Publication uses `mv -T`, so a concurrent bundle with the same final name fails instead of being nested inside an existing directory. Failed backups are removed by the exit trap and cannot remain under a plausible final name.

## Rollback states

`scripts/deploy/rollback_release.sh` requires `ROLLBACK_CONFIRM=I_UNDERSTAND` and emits `nexus_operator_rollback_result_v1`.

Possible states are:

- `INSTRUCTIONS_ONLY` — no mutation input was provided;
- `DATABASE_RESTORE_APPLIED` — the single-transaction `pg_restore` completed, so the target database was mutated even if post-restore identity verification later fails;
- `DATABASE_RESTORED` — the applied restore subsequently passed exact Alembic-head verification;
- `IMAGE_RESTARTED` — the requested old image tag was applied through Docker Compose;
- `HEALTH_VERIFIED` — both `/healthz` and `/readyz` returned explicit HTTP 2xx responses after restart.

The result also includes `outcome`, a fixed `failure_stage`, and Boolean fields that distinguish restore application from verified restoration. An EXIT trap writes partial state when a later operation fails. For example, if `pg_restore` commits but the Alembic post-check fails, the result records `DATABASE_RESTORE_APPLIED`, `outcome=fail`, `failure_stage=DATABASE_POST_VERIFY`, and `database_restored=false`. If the image restarts but a health check fails or redirects, it records `IMAGE_RESTARTED`, `outcome=fail`, `failure_stage=HEALTH_VERIFICATION`, and `health_verified=false`.

The script never reports a generic completed state. An image rollback requires `ROLLBACK_HEALTH_URL`; a database restore requires a regular archive/manifest pair and exact checksum match.

## Disposable CI topology

The dedicated gate uses pgvector PostgreSQL 16 with three isolated login roles:

- `nexus_recovery_admin` — bootstrap role used only for role/database creation and ownership proof;
- `nexus_recovery_source` — non-admin owner/client for `nexus_source`;
- `nexus_recovery_restore` — non-admin owner/client for `nexus_restore`.

The sequence is:

1. Validate explicit URL authority and pairwise-distinct role identity.
2. Prove source/restore are non-admin and admin has database-creation authority.
3. Recreate `nexus_source` and `nexus_restore` with different owners and verify ownership.
4. Upgrade source to the single current Alembic head.
5. Downgrade one revision to simulate an interrupted/outdated state.
6. Emit a bounded dry-run plan whose only repair action is `alembic_upgrade_head`.
7. Re-upgrade and verify the exact expected head.
8. Insert one synthetic Market/Team referential marker.
9. Snapshot every public table count, Alembic head, marker count, unvalidated-FK count and a deterministic SHA-256 signature for every public foreign-key definition.
10. Create and validate the real operator backup bundle as the source role.
11. Restore through the real rollback script into the restore-owned database.
12. Compare exact head, complete table/count set, marker and FK state.
13. Measure synthetic RPO and restore RTO.
14. Remove all backup bytes.
15. Scan bounded JSON evidence before upload.

## Evidence

Uploaded JSON may contain only source SHA, Alembic revision, public table names and aggregate counts, hashed foreign-key definitions, backup digest, bounded durations, Boolean integrity results and fixed reason codes. It excludes raw constraint definitions, backup bytes, connection strings and business rows. Timestamp order is validated before RTO/RPO acceptance; reversed timestamps fail closed rather than becoming zero-second evidence.

The disposable thresholds are:

- RTO: 120 seconds;
- RPO: 60 seconds;
- row-count mismatch: zero;
- unvalidated foreign keys: zero;
- foreign-key definition signatures: exact source/restore match;
- Alembic heads: exactly one matching head.

These are CI qualification thresholds, not production SLOs.

## Failure semantics

The gate fails closed on missing/ambient user identity, role collision or privilege mismatch, database-owner mismatch, migration/restore errors, missing/multiple/invalid heads, cluster mismatch, URI authority overrides, manifest/checksum mismatch, non-2xx health responses, missing marker, table/count mismatch, invalid or mismatched foreign-key signatures, unvalidated foreign keys, non-monotonic timing, RTO/RPO breach, unsafe evidence or missing evidence. Backup bytes are deleted even when qualification fails.

## Remaining work

Tenant-scoped retention, DSAR, anonymization and legal hold remain blocked on #546. Final #533 acceptance must rerun recovery against the immutable release candidate and realistic staging volume with approved encrypted backup custody, operator ownership and rollback rehearsal.

No merge or green check authorizes a production backup, restore, deletion, deployment, image restart, release tag, Provider action or real outbound.
