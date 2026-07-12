# Tenant principal preflight reconstruction

Work Item: #546

This branch reconstructs the previously reviewed Tenant ownership qualification logic against current `main@cdecaf955a5a04f948b0346815c9be0c5579805d` and Alembic head `20260711_0058`.

The delivery is intentionally limited to a read-only migration design gate. It introduces no Tenant table, foreign key, backfill, RLS policy, runtime authorization change, Provider action, outbound action or production-data mutation.

Acceptance requires:

- exact current-schema inventory;
- explicit mapping-driven ownership only;
- fail-closed null, empty, `default`, unknown and contradictory provenance;
- bounded count/reason/hash-only reports;
- disposable PostgreSQL pass/fail evidence;
- removal of all reconstruction bootstrap files before review readiness.
