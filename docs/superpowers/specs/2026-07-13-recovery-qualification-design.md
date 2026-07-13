# Recovery Qualification Design

## Status and authority

- Work Item: #532
- Parent Epic: #501
- Baseline: `main@7006af1e88d7681713cfd5ad4b540a3964d780f1`
- Historical PR #608: closed without merge; evidence only
- Delivery class: disposable PostgreSQL qualification plus operator-script correctness

## Problem

The repository had no accepted current-main backup/restore rehearsal. The operator scripts and evidence builder exposed sixteen reproducible failure modes:

1. a SQLAlchemy `postgresql+psycopg://` URL was passed directly to native clients;
2. restore could continue after SQL errors;
3. image rollback printed instructions but reported completion without restart or health verification;
4. failed backups could leave a plausible final `.sql.gz` artifact;
5. the disposable runner could issue destructive database commands through ambient libpq defaults rather than supplied URLs;
6. a health-check failure after image restart could exit without recording the partial mutation;
7. concurrent same-second backup publication could nest one bundle inside another final directory;
8. libpq URI query parameters could override the visibly validated host, port or database;
9. HTTP redirects could be accepted as successful health responses;
10. a regression asserted the fake `psql` marker only after its temporary directory had already been removed;
11. a successful transactional restore followed by a failed Alembic post-check could omit the already-applied database mutation from rollback state;
12. standalone backup/rollback entrypoints accepted libpq query or fragment overrides even though the disposable runner rejected them;
13. reversed recovery timestamps could be clamped to zero and accepted as bounded RTO/RPO evidence;
14. source/restore comparison checked only unvalidated-FK counts and could miss deleted or altered FK definitions;
15. native URLs without userinfo could inherit `PGUSER`, changing the executing identity outside the reviewed URI;
16. the qualification topology used one role for admin, migration, backup and restore, so non-admin paths were never proven independent of destructive authority.

These defects prevent a defensible claim that a test candidate is recoverable.

## Decisions

### Native PostgreSQL authority

`POSTGRES_NATIVE_URL` is the native-client authority. Known SQLAlchemy prefixes may be normalized for backward compatibility, but native tools never receive the unmodified application-driver URI. Standalone backup and rollback entrypoints require explicit user, host and database identity and reject URI query strings and fragments before any native client call.

The disposable runner requires a separate `RECOVERY_ADMIN_NATIVE_URL` and `RECOVERY_ALLOW_DATABASE_RECREATE=I_UNDERSTAND`. It validates source/restore application and native URL pairs, exact disposable database names, one cluster, an isolated admin database and three exact pairwise-distinct users:

- `nexus_recovery_admin`;
- `nexus_recovery_source`;
- `nexus_recovery_restore`.

No ambient user or target parameter is accepted.

### Role and ownership separation

The PostgreSQL service bootstraps only `nexus_recovery_admin`. The workflow creates source and restore login roles with no SUPERUSER, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS capability. Before database recreation, the runner queries `pg_roles` and fails unless those restrictions and admin CREATEDB authority are present. It creates each disposable database with its corresponding non-admin owner and verifies `pg_database.datdba` before Alembic executes.

### Atomic backup bundle

A backup is a directory containing a validated custom archive and bounded manifest. Both are produced in a same-filesystem temporary directory. The directory is published with `mv -T` only after archive listing, digest, size, source identity and Alembic checks succeed. Concurrent publication to the same final path fails rather than nesting content.

### Transactional restore

The restore path validates regular files, manifest schema/format/name/size/SHA-256/source identity and Alembic head before invoking `pg_restore --exit-on-error --single-transaction --clean --if-exists`. A post-restore `psql --set ON_ERROR_STOP=1` query verifies migration identity. Restore to a database with the same hashed name as the source is refused unless separately acknowledged.

### Explicit rollback state

Rollback output is structured. `DATABASE_RESTORE_APPLIED` is emitted immediately after a successful single-transaction `pg_restore`; `DATABASE_RESTORED` is emitted only after the exact Alembic-head post-check also succeeds. `IMAGE_RESTARTED` and `HEALTH_VERIFIED` are emitted only after their operations succeed. An EXIT trap writes `outcome=fail`, a fixed `failure_stage`, and all completed states when a later operation fails. Health verification requires explicit HTTP 2xx from both endpoints; redirects are failures. No generic success string is allowed.

### Migration repair

The qualification simulates an outdated/interrupted migration by downgrading one revision. The dry-run planner does not stamp or mutate. It emits only `alembic_upgrade_head` for a single valid older head and rejects missing, multiple or invalid heads. The disposable workflow then applies the canonical Alembic upgrade and verifies the expected head.

### Evidence boundary

Evidence contains aggregate schema identity and timing only. Public foreign-key definitions are represented only by deterministic SHA-256 signatures and must match exactly between source and restore. RTO/RPO timestamps must be monotonic; impossible ordering is an acceptance failure. Backup bytes, connection strings and business rows are removed before upload. The existing artifact scanner is the upload gate. Full evidence uploads only after a clean scan; an unsafe scan uploads only sanitized numeric status.

## Qualification topology

One pgvector PostgreSQL 16 service hosts one bootstrap admin database and two disposable databases with distinct owners. Source migration/backup uses only `nexus_recovery_source`; target restore/snapshot uses only `nexus_recovery_restore`; database creation and ownership proof use only `nexus_recovery_admin`. The source is seeded with a synthetic referential marker and the restore database is rebuilt exclusively from the operator backup.

## Security and privacy

- no production database or credentials;
- no ambient connection user or query-parameter authority;
- no customer, ticket, message, tracking, address or Provider data;
- no backup bytes uploaded;
- no deployment or real image restart in CI;
- all Actions are immutable and permissions read-only;
- failure evidence is bounded and customer-data free.

## Rejected approaches

- Passing SQLAlchemy URLs directly to libpq tools: incompatible and ambiguous.
- Omitting userinfo and allowing `PGUSER` fallback: executed identity is not bound to the reviewed URL.
- Sharing one database role across admin/source/restore: cannot prove least-privilege separation.
- Using ambient `PGHOST`/`PGUSER` for destructive setup: not bound to the supplied disposable topology.
- Accepting libpq query or fragment overrides: the executed target can differ.
- Plain SQL gzip as the canonical backup: weaker archive validation and restore control.
- Direct write to the final backup filename: leaves partial artifacts.
- Directory-target `mv` semantics: can hide concurrent publication by nesting a bundle.
- Alembic `stamp` as automatic repair: can hide schema drift.
- Treating printed rollback instructions as completion: no operational proof.
- Writing rollback status only on success: loses partial-mutation evidence.
- Treating an applied restore as unmodified merely because the post-restore identity check failed.
- Treating redirects as readiness proof.
- Comparing only the count of unvalidated foreign keys: missing or altered FK definitions can still appear valid.
- Clamping reversed timestamps to zero: impossible evidence must fail closed.

## Acceptance

One exact final Head must prove focused tests, explicit user/target authority, isolated role privileges and database ownership, real disposable migration roundtrip, dry-run repair planning, real operator backup and rollback scripts, exact 2xx health semantics, exact table/FK restore comparison, monotonic RTO/RPO thresholds, failure-state persistence, safe evidence, repository checks and independent review.

## Rollback

Revert the additive qualification files and restore the two operator scripts. No database downgrade, production repair or external cleanup is required because the workflow uses disposable databases only.
