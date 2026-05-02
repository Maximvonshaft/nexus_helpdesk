# Incident Playbook

## P0: external outbound unexpectedly sends

Immediate actions:

1. Set ENABLE_OUTBOUND_DISPATCH=false.
2. Set OUTBOUND_PROVIDER=disabled.
3. Stop worker containers.
4. Inspect ticket_outbound_messages for processing or pending external rows.
5. Preserve logs and database state for audit.

Verification:

- provider-level dispatch gate tests pass
- no OpenClaw send path is called when provider is disabled

## P1: migration failure

Immediate actions:

1. Stop rollout.
2. Keep the previous app and worker image running if possible.
3. Capture Alembic error output.
4. Check alembic heads and current revision.
5. Do not reset or drop production data.

Verification:

- alembic heads
- alembic upgrade head on staging copy
- python scripts/check_model_migration_drift.py

## P1: WebChat public runtime failure

Immediate actions:

1. Check WebChat allowed origins.
2. Check visitor token transport via X-Webchat-Visitor-Token.
3. Check browser console for CORS or 403 responses.
4. Verify /api/webchat/init works.
5. Verify send and poll use the same conversation and token.

Recovery:

- expired visitor tokens should trigger a new init path
- widget should clear local state and recover a new session

## P2: OpenClaw inbound sync degradation

Immediate actions:

1. Confirm healthz and readyz.
2. Inspect OpenClaw runtime health.
3. Check unresolved events.
4. Confirm the inbound sync daemon or scheduled sync path is alive.

Do not enable outbound dispatch as a workaround for inbound sync issues.

## Evidence to collect

- commit SHA
- deployed image tag
- Alembic current revision
- service logs
- healthz and readyz output
- affected ticket ids
- affected conversation ids
- outbound message ids if relevant
