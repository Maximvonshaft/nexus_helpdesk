# Current-main reconstruction

This migration gate was reconstructed from historical PR #609 against `main@cdecaf955a5a04f948b0346815c9be0c5579805d`. Historical code is evidence only. The accepted baseline is Alembic `20260711_0058`, six core ownership tables without Tenant foreign keys, and eight existing operational `tenant_id`/`tenant_key` columns. No next migration revision is reserved until this preflight is accepted.

# Nexus OSR Authoritative Tenant Principal Migration

## Decision

Tenant becomes a server-owned relational principal. Client strings, UI filters,
country codes, Market codes and the legacy literal `default` are not Tenant
authority.

No structural migration may infer that all historical records belong to one
Tenant. Historical ownership is accepted only through an explicit, reviewed
mapping manifest and a fail-closed full-data preflight.

## Required ownership chain

The target model is:

```text
Tenant
 ├─ Market
 │  ├─ Team
 │  │  └─ User
 │  └─ ChannelAccount
 ├─ Customer
 └─ Ticket
```

Every protected Runtime, Knowledge, Case Context, Audit, Tool and Dispatch
record must resolve to the same Tenant as its owning Ticket/Conversation or
approved Tenant-scoped resource.

## Preflight mapping contract

Schema: `nexus_tenant_backfill_mapping_v1`

```json
{
  "schema_version": "nexus_tenant_backfill_mapping_v1",
  "tenants": [
    {"tenant_key": "tenant-me", "display_name": "Montenegro Operations"}
  ],
  "market_codes": {"ME": "tenant-me"},
  "team_ids": {},
  "user_ids": {},
  "channel_account_ids": {},
  "ticket_ids": {},
  "customer_ids": {}
}
```

Rules:

- Tenant keys are stable lowercase identifiers, are at most 80 characters, and cannot be `default`.
- A persisted Tenant key must already equal `lower(trim(value))`; leading or trailing whitespace is rejected rather than silently normalized.
- Every Market requires an explicit code mapping.
- Team, User and ChannelAccount normally inherit through organization links;
  explicit ID mappings are allowed only for disconnected records and must not
  contradict an inferred relationship.
- Ticket ownership is the union of Market, Team, ChannelAccount, Assignee and
  Creator provenance. More than one candidate Tenant is a blocking conflict.
- Customer ownership is derived from every linked Ticket. A Customer used by
  multiple Tenants must be split or otherwise remediated before migration;
  explicit mapping cannot hide that conflict.
- Empty, unknown, unused or conflicting mappings fail.
- Existing `tenant_id`/`tenant_key` values that are null, empty, `default` or not
  declared by the manifest fail.
- Reports contain counts, reason codes and hashed record samples only.

The manifest is deployment-specific data. A production mapping must be stored
in approved secure change custody, not committed to the repository.

## Phase 1 — additive schema

After the preflight is accepted on the target dataset:

1. Create `tenants` with a stable internal primary key and unique immutable
   `tenant_key`.
2. Add nullable `tenant_id` foreign keys and indexes to Market, Team, User,
   ChannelAccount, Customer and Ticket.
3. Add provenance columns needed to record how each assignment was resolved.
4. Do not change application reads or writes yet.
5. Upgrade/downgrade/re-upgrade on PostgreSQL and run dirty-data preflight.

The additive migration must not insert a default Tenant or backfill records.

## Phase 2 — explicit bounded backfill

A separate command consumes the approved mapping manifest and:

- validates the manifest digest and exact source schema revision;
- updates records in bounded, resumable batches;
- writes only Tenant IDs and safe provenance codes;
- is idempotent;
- emits a signed/bounded receipt with assigned counts and unresolved hashes;
- stops on the first conflict or relationship drift;
- supports dry-run before write mode.

Customer cross-Tenant conflicts must be repaired before this phase can pass.

## Phase 3 — dual-read and dual-write enforcement

Application repositories resolve Tenant through the relational principal and
compare it with all legacy Tenant fields. During the transition:

- missing or conflicting ownership fails before read or persistence;
- API principals carry one authorized Tenant scope;
- background jobs, cache keys, idempotency keys and metrics include Tenant;
- Knowledge, Tracking, Tool and Dispatch paths verify the same Tenant;
- cross-Tenant negative tests cover HTTP, WebSocket, Worker and export paths;
- legacy string fields remain diagnostic only and cannot authorize access.

## Phase 4 — constraints and legacy retirement

Only after full-data verification:

- make core `tenant_id` columns non-null;
- add Tenant-consistency constraints or triggers where relational chains cannot
  be represented by a simple foreign key;
- remove application reliance on unconstrained legacy Tenant strings;
- reject creation of `default` Tenant values;
- publish one authoritative Tenant repository/service boundary;
- perform a downgrade rehearsal and data-repair rehearsal.

## PostgreSQL RLS decision

RLS is selected as a **defense-in-depth target**, not the first migration step.
It is enabled only after:

1. core Tenant FKs are non-null and backfill receipts are accepted;
2. every request and Worker transaction sets a trusted database Tenant context;
3. migrations, maintenance, restore and reconciliation roles have explicit
   bypass policy;
4. connection pooling proves Tenant context is reset between transactions;
5. RLS integration tests cover API, Worker and background-task access;
6. administrative cross-Tenant access requires a separate audited role.

Before those conditions, enabling partial RLS would create inconsistent and
potentially unsafe behavior. Application-level Tenant scoping remains mandatory
even after RLS is enabled.

## Release blockers

Pilot and Full OSR readiness remain `not_configured` while any of the following
is true:

- preflight contains unresolved records or conflicts;
- mapping approval or digest is missing;
- additive/backfill/enforcement migrations are incomplete;
- legacy `default` Tenant values remain;
- cross-Tenant negative tests fail;
- Worker/Provider paths cannot prove Tenant propagation;
- recovery qualification has not been rerun on the resulting schema.

No document, PR or green CI in this migration stream authorizes production
backfill, deployment, Provider enablement or real outbound.

## Phase 1 implementation contract (`20260713_0059`)

The first structural revision is intentionally limited to a reversible schema
foundation:

- `tenants` stores an internal integer identity, immutable unique `tenant_key`,
  display name, active state and audit timestamps;
- `tenant_key` is bounded to 80 characters and must already equal `lower(trim(value))`; empty, padded, mixed-case and padded-`default` values fail closed;
- Market, Team, User, ChannelAccount, Customer and Ticket receive nullable
  `tenant_id` foreign keys with `RESTRICT` deletion and dedicated indexes;
- each core table receives nullable `tenant_assignment_source` and
  80-character `tenant_assignment_version` fields so a full `sha256:<64hex>` receipt fits without truncation;
- ORM relationships expose the principal but no runtime authorization path is
  switched to it in this phase;
- the migration inserts no Tenant, applies no default and updates no historical
  row;
- downgrade removes only the new ownership structure while preserving all core
  records; re-upgrade recreates empty nullable ownership fields;
- the preflight resolves relational integer IDs through `tenants.tenant_key`
  and compares them with manifest-derived ownership instead of treating IDs as
  client-provided Tenant strings.

This revision is not a rollout authorization. Phase 2 still requires an
approved deployment mapping, dry-run receipt, bounded apply command and explicit
handling of every unresolved or cross-Tenant record.
