# Nexus OSR Recovery Qualification

## Purpose

This control proves that an exact Nexus revision can be migrated, backed up, restored into a fresh PostgreSQL database and verified without using production data. It supports #532 and the final #533 release decision; it is not a deployment or production-restore authorization.

## Connection authority

Application code uses a SQLAlchemy URL such as `postgresql+psycopg://...`. Native PostgreSQL tools use `POSTGRES_NATIVE_URL` in libpq form: `postgresql://...`.

The operator scripts accept `POSTGRES_NATIVE_URL` directly. For compatibility they may normalize the known SQLAlchemy driver prefixes from `DATABASE_URL`, but they never pass an unmodified `postgresql+psycopg://` URL to `pg_dump`, `pg_restore` or `psql`.

## Backup bundle

`scripts/deploy/backup_postgres.sh` creates one mode-restricted temporary directory containing:

- `database.dump` — custom-format, compressed, owner/privilege-free PostgreSQL archive;
- `backup_manifest.json` — schema version, archive digest and size, hashed source-database identity, exact Alembic head and creation time.

The archive is non-empty and must pass `pg_restore --list`. The manifest and SHA-256 are generated before the temporary directory is atomically renamed to its final bundle path. Failed backups are removed by the exit trap and cannot remain under a plausible final name.

## Rollback states

`scripts/deploy/rollback_release.sh` requires `ROLLBACK_CONFIRM=I_UNDERSTAND` and emits `nexus_operator_rollback_result_v1`.

Possible states are:

- `INSTRUCTIONS_ONLY` — no mutation input was provided;
- `DATABASE_RESTORED` — manifest/checksum/archive validation and transactional `pg_restore` succeeded;
- `IMAGE_RESTARTED` — the requested old image tag was applied through Docker Compose;
- `HEALTH_VERIFIED` — both `/healthz` and `/readyz` succeeded after restart.

The script never reports a generic completed state. An image rollback requires `ROLLBACK_HEALTH_URL`; a database restore requires a regular archive/manifest pair and exact checksum match.

## Disposable CI topology

The dedicated gate uses pgvector PostgreSQL 16 and creates:

- `nexus_source` — migrated using the repository's current Alembic chain;
- `nexus_restore` — empty target restored only from the generated backup bundle.

The sequence is:

1. Upgrade source to the single current Alembic head.
2. Downgrade one revision to simulate an interrupted/outdated state.
3. Emit a bounded dry-run plan whose only repair action is `alembic_upgrade_head`.
4. Re-upgrade and verify the exact expected head.
5. Insert one synthetic Market/Team referential marker.
6. Snapshot every public table count, Alembic head, marker count and unvalidated-FK count.
7. Create and validate the real operator backup bundle.
8. Restore through the real rollback script into the disposable target.
9. Compare exact head, complete table/count set, marker and FK state.
10. Measure synthetic RPO and restore RTO.
11. Remove all backup bytes.
12. Scan bounded JSON evidence before upload.

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

The gate fails closed on migration/restore errors, multiple or invalid heads, manifest/checksum mismatch, missing marker, table/count mismatch, unvalidated foreign keys, RTO/RPO breach, unsafe evidence or missing evidence. Backup bytes are deleted even when qualification fails.

## Remaining work

Tenant-scoped retention, DSAR, anonymization and legal hold remain blocked on #546. Final #533 acceptance must rerun recovery against the immutable release candidate and realistic staging volume with approved encrypted backup custody, operator ownership and rollback rehearsal.

No merge or green check authorizes a production backup, restore, deletion, deployment, image restart, release tag, Provider action or real outbound.
