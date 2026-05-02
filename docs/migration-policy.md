# Migration Policy

## Purpose

NexusDesk must not merge SQLAlchemy model or API changes without matching Alembic migrations. The AI Config schema repair in `20260502_0014` showed that model/API drift can produce production `UndefinedTable` failures even when the database is already at Alembic head.

## Required rules

1. Every new SQLAlchemy table must have an explicit Alembic revision.
2. Every new model column used by runtime code must have an explicit Alembic revision.
3. Production migrations must be run before the new app/worker image handles traffic.
4. `Base.metadata.create_all()` tests are not enough. They do not prove the migration chain creates the production schema.
5. Drift exceptions must be centralized in `backend/scripts/check_model_migration_drift.py` and include a reason.

## Required verification

```bash
cd backend
alembic heads
alembic upgrade head
python scripts/check_model_migration_drift.py
pytest -q tests/test_migration_drift_gate.py
```

## Current WebChat runtime migration

`20260502_0015_webchat_runtime_hardening.py` adds:

- `webchat_conversations.visitor_token_expires_at`
- `webchat_messages.client_message_id`
- `uq_webchat_message_client_id`

This migration is required before deploying the WebChat runtime hardening code.
