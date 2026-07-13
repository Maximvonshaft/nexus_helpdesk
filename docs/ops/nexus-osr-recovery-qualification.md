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
- the source/restore roles exist, can log in, inherit database-owner privileges and have no SUPERUSER, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS privilege;
- the bootstrap admin is the explicit superuser/CREATEDB authority needed for database creation and installation of the untrusted `vector` extension.

The admin creates each disposable database from `template0` with its corresponding non-admin role as owner, and the runner verifies those owners before Alembic starts. Ambient `PGHOST`, `PGUSER`, `PGPASSWORD` or default-database state is not destructive authority.

## Preinstalled PostgreSQL extension

The existing Alembic chain contains `CREATE EXTENSION IF NOT EXISTS vector`, but a fresh non-superuser owner cannot perform the first installation of an untrusted extension. The qualification therefore uses a narrow bootstrap boundary:

1. `nexus_recovery_admin` installs `vector` into both empty disposable databases;
2. source and restore roles prove the same extension version and admin ownership through `pg_extension`;
3. the backup manifest records exactly one preinstalled extension, `vector`, and its version;
4. restore fails before mutation unless the target already contains the same version;
5. a restore TOC is generated from the checksum-validated archive and comments out exactly the `EXTENSION - vector` entry and at most one extension comment entry;
6. any missing, duplicate or unrecognized vector extension TOC entry fails closed;
7. all remaining objects still restore with `--clean --if-exists --single-transaction --no-owner --no-privileges` as `nexus_recovery_restore`;
8. post-restore verification rechecks the exact vector version.

The filtered TOC is temporary, mode-restricted and deleted on success or failure. It is not uploaded.

## Clean restore target

`--clean` removes only objects represented in the archive; it cannot remove unrelated objects that exist only in the target. Before generating the restore TOC or calling `pg_restore`, the rollback script therefore proves that the target contains no non-system schema other than `public` and no public relation, partitioned table, view, materialized view, sequence, foreign table, index or composite relation outside the preinstalled extension dependency graph. A target with any such footprint fails with `rollback_target_not_empty` before mutation.

This is a strict disposable-target contract. The operator must recreate or separately clean a non-empty target rather than relying on archive restore to erase unknown state.

## Backup bundle

`scripts/deploy/backup_postgres.sh` creates one mode-restricted temporary directory containing:

- `database.dump` — custom-format, compressed, owner/privilege-free PostgreSQL archive;
- `backup_manifest.json` — schema version, archive digest and size, hashed source-database identity, exact Alembic head, exact preinstalled vector version and creation time.

The archive is non-empty and must pass `pg_restore --list`. The manifest and SHA-256 are generated before the temporary directory is atomically renamed to its final bundle path. Publication uses `mv -T`, so a concurrent bundle with the same final name fails instead of being nested inside an existing directory. Failed backups are removed by the exit trap and cannot remain under a plausible final name.

## Rollback states

`scripts/deploy/rollback_release.sh` requires `ROLLBACK_CONFIRM=I_UNDERSTAND` and emits `nexus_operator_rollback_result_v1`.

Possible states are:

- `INSTRUCTIONS_ONLY` — no mutation input was provided;
- `DATABASE_RESTORE_APPLIED` — the single-transaction `pg_restore` completed, so the target database was mutated even if post-restore identity verification later fails;
- `DATABASE_RESTORED` — the applied restore subsequently passed exact Alembic-head and vector-version verification;
- `IMAGE_RESTARTED` — Docker Compose pulled the requested old image and restarted the bounded service set without any local build fallback;
- `HEALTH_VERIFIED` — both `/healthz` and `/readyz` returned explicit HTTP 2xx responses after restart.

The result also includes `outcome`, a fixed `failure_stage`, and Boolean fields that distinguish restore application from verified restoration. An EXIT trap writes partial state when a later operation fails. For example, if `pg_restore` commits but the Alembic post-check fails, the result records `DATABASE_RESTORE_APPLIED`, `outcome=fail`, `failure_stage=DATABASE_POST_VERIFY`, and `database_restored=false`. If the image restarts but a health check fails or redirects, it records `IMAGE_RESTARTED`, `outcome=fail`, `failure_stage=HEALTH_VERIFICATION`, and `health_verified=false`.

Image rollback uses `docker compose up --no-build --pull always`; an unavailable old image is a hard failure rather than permission to rebuild locally. The script never reports a generic completed state. An image rollback requires `ROLLBACK_HEALTH_URL`; a database restore requires a regular archive/manifest pair, exact checksum, matching preinstalled vector version and a clean target.

## Disposable CI topology

The dedicated gate uses pgvector PostgreSQL 16 with three isolated login roles:

- `nexus_recovery_admin` — bootstrap superuser used only for role/database creation, ownership proof and vector installation;
- `nexus_recovery_source` — non-admin owner/client for `nexus_source`;
- `nexus_recovery_restore` — non-admin owner/client for `nexus_restore`.

The sequence is:

1. Validate explicit URL authority and pairwise-distinct role identity.
2. Prove source/restore are non-admin and admin has the required bootstrap authority.
3. Recreate `nexus_source` and `nexus_restore` from `template0` with different owners and verify ownership.
4. Preinstall and prove the same admin-owned vector version in both databases.
5. Upgrade source to the single current Alembic head as the source role.
6. Downgrade one revision to simulate an interrupted/outdated state.
7. Emit a bounded dry-run plan whose only repair action is `alembic_upgrade_head`.
8. Re-upgrade and verify the exact expected head.
9. Insert one synthetic Market/Team referential marker.
10. Snapshot every public table count, Alembic head, marker count, unvalidated-FK count and a deterministic SHA-256 signature for every public foreign-key definition.
11. Create and validate the real operator backup bundle as the source role.
12. Prove the restore target has no user schema/relation footprint beyond the preinstalled extension.
13. Restore through the real rollback script as the restore role while preserving the manifest-bound preinstalled extension.
14. Compare exact head, complete table/count set, marker and FK state.
15. Measure synthetic RPO and restore RTO.
16. Remove all backup bytes and temporary TOC files.
17. Scan bounded JSON evidence before upload.

## Evidence

Uploaded JSON may contain only source SHA, Alembic revision, public table names and aggregate counts, hashed foreign-key definitions, backup digest, bounded durations, Boolean integrity results and fixed reason codes. It excludes raw constraint definitions, extension TOC contents, backup bytes, connection strings and business rows. Timestamp order is validated before RTO/RPO acceptance; reversed timestamps fail closed rather than becoming zero-second evidence.

The disposable thresholds are:

- RTO: 120 seconds;
- RPO: 60 seconds;
- row-count mismatch: zero;
- unvalidated foreign keys: zero;
- foreign-key definition signatures: exact source/restore match;
- Alembic heads: exactly one matching head;
- vector version: exact source/target/post-restore match;
- target user schema/relation footprint before restore: zero.

These are CI qualification thresholds, not production SLOs.

## Failure semantics

The gate fails closed on missing/ambient user identity, role collision or privilege mismatch, database-owner mismatch, vector bootstrap/version/TOC mismatch, non-empty restore target, migration/restore errors, missing/multiple/invalid heads, cluster mismatch, URI authority overrides, manifest/checksum mismatch, unavailable exact rollback image, local-build fallback, non-2xx health responses, missing marker, table/count mismatch, invalid or mismatched foreign-key signatures, unvalidated foreign keys, non-monotonic timing, RTO/RPO breach, unsafe evidence or missing evidence. Backup bytes and temporary TOC files are deleted even when qualification fails.

## Remaining work

Tenant-scoped retention, DSAR, anonymization and legal hold remain blocked on #546. Final #533 acceptance must rerun recovery against the immutable release candidate and realistic staging volume with approved encrypted backup custody, operator ownership and rollback rehearsal.

No merge or green check authorizes a production backup, restore, deletion, deployment, image restart, release tag, Provider action or real outbound.
