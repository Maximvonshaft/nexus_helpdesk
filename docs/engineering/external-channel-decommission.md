# ExternalChannel retirement boundary

## Decision

ExternalChannel is not a supported runtime transport authority. Application routes and workers must not create ExternalChannel links, cursors, unresolved events, attachment records, background jobs or provider traffic.

Current authority is defined by:

- `config/architecture/service-authority.v1.json` for backend public/core ownership;
- `config/architecture/compatibility-lifecycle.v1.json` for compatibility policy and deadlines;
- `config/governance/legacy-surface-domains.v2.json` for current-tree discovery;
- `scripts/verify_repository.py` and `backend/tests/test_external_channel_final_retirement.py` for anti-reintroduction;
- Alembic for all schema changes.

No document or environment example may authorize re-enabling the retired runtime.

## Fail-closed environment detection

Any non-disabled value for a retired ExternalChannel execution switch is a startup error. Disabled configuration is a tombstone detector, not a dormant fallback implementation.

The controlled deployment surface must contain no ExternalChannel URL, token, password, command, bridge, sync or event-driver configuration.

## Allowed persistence boundary

Historical model and table names may remain only as a protected data-migration dependency until all of the following exist:

1. zero-new-write and zero-runtime-caller proof on an exact release candidate;
2. bounded historical export and reconciliation;
3. backup and restore rehearsal;
4. a reviewed destructive Alembic migration;
5. explicit authorization for the exact destructive action.

These persisted names are not transport authority. They may not be used as a rollback mechanism.

## Canonical channel non-regression

ExternalChannel retirement must not alter the active channel architecture:

- customer-visible messages remain behind the canonical Provider and outbound governance boundaries;
- Provider routing and traffic selection remain configuration-driven;
- WhatsApp owns only its current connector/channel lifecycle;
- tests and migration tools cannot send real outbound traffic;
- repository verification never enables a Provider or mutates production.

## Evidence

Any final data/schema retirement must bind caller inventory, observed write counts, export identity, backup/restore evidence, row reconciliation, migration rehearsal, rollback procedure, source SHA and tree SHA. Evidence contains identifiers, counts and hashes, never customer payloads or credentials.
