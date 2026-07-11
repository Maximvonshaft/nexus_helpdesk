# Nexus OSR PostgreSQL Recovery Qualification

## Purpose

This qualification proves that one immutable Nexus OSR source revision can be
migrated, backed up, restored into a fresh PostgreSQL database and verified
without using production data or mutating a production system.

It is evidence for Work Item #532. It is not a deployment, production restore,
release authorization or final M12 acceptance.

## Test topology

The permanent GitHub Actions gate creates two disposable PostgreSQL 16
databases:

- `nexus_source` — upgraded through the repository's exact Alembic head;
- `nexus_restore` — recreated empty and populated only by `pg_restore`.

The source database receives one synthetic Market and one related Team so the
restore proves both row preservation and a real foreign-key relationship. No
customer, ticket, message, tracking, address, credential or Provider data is
used.

## Qualification sequence

1. Checkout the exact PR head.
2. Upgrade `nexus_source` to the single current Alembic head.
3. Insert the bounded synthetic marker.
4. Snapshot every public table name and row count, the Alembic head, foreign-key
   validation state and marker count.
5. Produce a custom-format, compressed, owner-free PostgreSQL backup.
6. Bind the backup to a SHA-256 digest; never upload the backup bytes.
7. Recreate `nexus_restore` from empty state and run `pg_restore --exit-on-error`.
8. Snapshot the restored database using the same contract.
9. Compare exact head, table set, every row count, marker presence and validated
   foreign-key state.
10. Measure the synthetic transaction-to-backup interval and restore duration.
11. Scan the bounded JSON evidence for prohibited material.
12. Delete backup bytes before artifact upload.

## Initial targets

- Test RTO: restore completes within 120 seconds.
- Test RPO: the synthetic committed transaction is captured within 60 seconds.
- Data loss: zero mismatched table counts.
- Referential integrity: zero unvalidated foreign-key constraints.
- Identity: source and restored databases have one matching Alembic head.

These are qualification thresholds for the disposable CI dataset, not final
production SLOs. Work Item #533 must approve production RTO/RPO using realistic
staging volume and the exact final candidate.

## Evidence schema

The uploaded artifact contains only:

- source SHA;
- Alembic revision;
- public table names and aggregate row counts;
- backup SHA-256;
- RTO/RPO targets and observed durations;
- Boolean integrity/marker results;
- bounded failure reason codes;
- the artifact safety scan.

It excludes backup bytes and all business rows.

## Failure semantics

The gate fails when any of the following occurs:

- migration or restore error;
- zero or multiple Alembic heads;
- source/restored table or row-count mismatch;
- missing synthetic marker;
- unvalidated foreign keys;
- RTO or RPO threshold exceeded;
- invalid backup digest or source identity;
- prohibited material in evidence.

No exception or green general CI check may override the owning recovery gate.

## Final-candidate use

This workflow must be rerun after accepted schema changes and again on the
immutable #533 candidate. Final release control must additionally prove:

- realistic data volume;
- encrypted backup custody and access control;
- staging restore to the supported deployment topology;
- operator runbook execution and incident ownership;
- retention, cleanup and DSAR behavior after first-class Tenant authority;
- rollback and application usability after restore.
