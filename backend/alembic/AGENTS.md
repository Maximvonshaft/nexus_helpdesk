# backend/alembic/AGENTS.md — Migration Execution Contract

This contract applies to `backend/alembic/**`. Schema migrations are production-risk changes. Treat them as immutable deployment artifacts once they may have been applied anywhere outside a disposable branch.

## 1. Mandatory inspection before migration changes

Before adding or editing a migration, inspect:

```text
backend/app/models.py
backend/alembic/env.py
backend/alembic/versions/**
backend/app/settings.py
backend/tests/** touching the affected model/table
```

Also inspect the service/API path that reads or writes the table.

## 2. Migration rules

- Every persistent model/table/index/constraint change requires an Alembic revision.
- Do not rely on runtime `create_all/drop_all` semantics for production schema changes.
- Do not silently edit old applied migrations.
- Revision IDs must be short enough for `alembic_version.version_num` and must not repeat prior IDs.
- Prefer explicit DDL operations over broad metadata reflection when possible.
- Include indexes for queue, lookup, pagination, idempotency, and high-cardinality access paths.
- If a downgrade is unsafe or impossible, document the operational rollback path in the migration comment and PR.

## 3. Table families to protect

High-risk table families include:

```text
users / auth_throttle_entries / user_capability_overrides
admin_audit_logs / admin_action_rate_limits
integration_clients / integration_request_logs
markets / channel_accounts / market_bulletins
tickets / ticket_comments / ticket_events / ticket_attachments / ticket_outbound_messages / ticket_ai_intakes
background_jobs
openclaw_conversation_links / openclaw_transcript_messages / openclaw_attachment_references / openclaw_sync_cursors / openclaw_unresolved_events
webchat_rate_limits
provider runtime / credential custody tables
webcall / voice session tables
Speedaf action/work-order tables
```

## 4. Backward compatibility

When adding a non-null column to a populated table:

```text
1. add nullable column or server_default
2. backfill safely if needed
3. only then enforce non-null in a later migration if required
```

Do not break older rows without a backfill strategy.

## 5. Required validation

Run from repository root or as indicated:

```bash
set -Eeuo pipefail
cd backend
PYTHONPATH=. alembic heads
PYTHONPATH=. alembic history --verbose
PYTHONPATH=. alembic upgrade head
```

Also run affected tests:

```bash
PYTHONPATH=backend pytest -q <targeted tests>
```

If a migration depends on PostgreSQL-specific behavior such as partial indexes or `FOR UPDATE SKIP LOCKED`, validate against PostgreSQL, not only SQLite.

## 6. PR evidence

Migration PRs must report:

```text
revision id
down_revision
new/changed tables
new/changed columns
new/changed indexes/constraints
upgrade validation result
downgrade status or rollback limitation
runtime compatibility impact
```
