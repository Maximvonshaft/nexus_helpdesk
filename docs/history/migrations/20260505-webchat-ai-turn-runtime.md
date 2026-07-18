# WebChat AI turn runtime migration history

The former files under `ops/migrations/20260505_webchat_ai_turn_runtime*.sql` were an early, manually executable candidate for the WebChat AI turn schema.

They are intentionally removed from executable repository paths. Nexus has one schema-mutation authority: Alembic.

## Canonical revision

- Revision: `20260505_0017_webchat_ai_runtime_p0`
- File: `backend/alembic/versions/20260505_0017_webchat_ai_runtime_p0.py`
- Upgrade and downgrade behavior: defined only by that Alembic revision and its descendants.

This document is historical evidence only. It is not a migration, rollback instruction, or production runbook. Database changes must use `alembic upgrade` / `alembic downgrade` against an explicitly rehearsed revision path.
