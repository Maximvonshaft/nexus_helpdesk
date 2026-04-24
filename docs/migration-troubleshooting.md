# Migration Troubleshooting

## Alembic revision length issue

The governance overlay migration must use the short revision id:

```text
20260421_gov_r4
```

Do not use the old long id:

```text
20260421_governance_overlay_round4
```

The old id can exceed PostgreSQL's default `alembic_version.version_num varchar(32)` length and break fresh migrations.

## If a failed database already recorded the old revision

Only after confirming the schema state, update the version table manually:

```sql
UPDATE alembic_version
SET version_num = '20260421_gov_r4'
WHERE version_num = '20260421_governance_overlay_round4';
```

Then run:

```bash
cd backend
alembic heads
alembic current
alembic upgrade head
```

Do not run this blindly against production. Back up first.
