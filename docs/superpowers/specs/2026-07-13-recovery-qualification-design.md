# Recovery Qualification Design

## Status and authority

- Work Item: #532
- Parent Epic: #501
- Baseline: `main@7b46e48e5cd26fc400728cb84dfe04b57d5fa855`
- Historical PR #608: closed without merge; evidence only
- Delivery class: disposable PostgreSQL qualification plus operator-script correctness

## Problem

The repository had no accepted current-main backup/restore rehearsal. The operator scripts also exposed seven reproducible failure modes:

1. a SQLAlchemy `postgresql+psycopg://` URL was passed directly to native clients;
2. restore could continue after SQL errors;
3. image rollback printed instructions but reported completion without restart or health verification;
4. failed backups could leave a plausible final `.sql.gz` artifact;
5. the disposable runner could issue destructive database commands through ambient libpq defaults rather than supplied URLs;
6. a health-check failure after image restart could exit without recording the partial mutation;
7. concurrent same-second backup publication could nest one bundle inside another final directory.

These defects prevent a defensible claim that a test candidate is recoverable.

## Decisions

### Native PostgreSQL authority

`POSTGRES_NATIVE_URL` is the native-client authority. Known SQLAlchemy prefixes may be normalized for backward compatibility, but native tools never receive the unmodified application-driver URI.

The disposable runner requires a separate `RECOVERY_ADMIN_NATIVE_URL` and an explicit `RECOVERY_ALLOW_DATABASE_RECREATE=I_UNDERSTAND`. It validates source/restore application and native URL pairs, requires all three native URLs to share one host/port, restricts source/restore names to `nexus_source` and `nexus_restore`, and requires the admin URL to target a third database before any destructive command.

### Atomic backup bundle

A backup is a directory containing a validated custom archive and bounded manifest. Both are produced in a same-filesystem temporary directory. The directory is published with `mv -T` only after archive listing, digest, size, source identity and Alembic checks succeed. Concurrent publication to the same final path fails rather than nesting content.

### Transactional restore

The restore path validates regular files, manifest schema/format/name/size/SHA-256/source identity and Alembic head before invoking `pg_restore --exit-on-error --single-transaction --clean --if-exists`. A post-restore `psql --set ON_ERROR_STOP=1` query verifies migration identity. Restore to a database with the same hashed name as the source is refused unless separately acknowledged.

### Explicit rollback state

Rollback output is structured. `DATABASE_RESTORED`, `IMAGE_RESTARTED` and `HEALTH_VERIFIED` are emitted only after their operations succeed. An EXIT trap writes `outcome=fail`, a fixed `failure_stage`, and all completed states when a later operation fails. No generic success string is allowed.

### Migration repair

The qualification simulates an outdated/interrupted migration by downgrading one revision. The dry-run planner does not stamp or mutate. It emits only `alembic_upgrade_head` for a single valid older head and rejects missing, multiple or invalid heads. The disposable workflow then applies the canonical Alembic upgrade and verifies the expected head.

### Evidence boundary

Evidence contains aggregate schema identity and timing only. Backup bytes, connection strings and business rows are removed before upload. The existing artifact scanner is the upload gate. Full evidence uploads only after a clean scan; an unsafe scan uploads only sanitized numeric status.

## Qualification topology

One pgvector PostgreSQL 16 service hosts one admin database and two disposable databases. The source is migrated and seeded with a synthetic referential marker; the restore database is rebuilt exclusively from the operator backup. Source and restore snapshots are compared exactly.

## Security and privacy

- no production database or credentials;
- no ambient connection authority for destructive setup;
- no customer, ticket, message, tracking, address or Provider data;
- no backup bytes uploaded;
- no deployment or real image restart in CI;
- all Actions are immutable and permissions read-only;
- failure evidence is bounded and customer-data free.

## Rejected approaches

- Passing SQLAlchemy URLs directly to libpq tools: incompatible and ambiguous.
- Using ambient `PGHOST`/`PGUSER` for destructive setup: not bound to the supplied disposable topology.
- Plain SQL gzip as the canonical backup: weaker archive validation and restore control.
- Direct write to the final backup filename: leaves partial artifacts.
- Directory-target `mv` semantics: can hide concurrent publication by nesting a bundle.
- Alembic `stamp` as automatic repair: can hide schema drift.
- Treating printed rollback instructions as completion: no operational proof.
- Writing rollback status only on success: loses partial-mutation evidence.

## Acceptance

One exact final Head must prove focused tests, explicit admin authority, real disposable migration roundtrip, dry-run repair planning, real operator backup and rollback scripts, exact restore comparison, RTO/RPO thresholds, failure-state persistence, safe evidence, repository checks and independent review.

## Rollback

Revert the additive qualification files and restore the two operator scripts. No database downgrade, production repair or external cleanup is required because the workflow uses disposable databases only.
