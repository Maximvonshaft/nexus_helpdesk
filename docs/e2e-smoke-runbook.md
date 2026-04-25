# NexusDesk Round A E2E Smoke Runbook

## Purpose

Round A smoke tests prove the critical NexusDesk x OpenClaw functional chains without sending real customer messages or requiring a real OpenClaw gateway by default.

## Default safety model

- Default mode is mock/dry-run safe.
- Scripts do not call real WhatsApp, Telegram, SMS, or customer channels.
- Test data uses a unique prefix.
- Missing live credentials result in `SKIP`, not fake `PASS`.

## Common environment

```bash
export NEXUSDESK_API_URL=http://127.0.0.1:18081
export NEXUSDESK_SMOKE_MODE=mock
export NEXUSDESK_SMOKE_PREFIX=round-a-local
export OPENCLAW_MOCK_MODE=1
```

Optional live integration variables:

```bash
export NEXUSDESK_INTEGRATION_CLIENT_ID=...
export NEXUSDESK_INTEGRATION_CLIENT_KEY=...
export NEXUSDESK_ADMIN_EMAIL=...
export NEXUSDESK_ADMIN_PASSWORD=...
```

## Run all dry-run checks

```bash
bash scripts/smoke/smoke_all_round_a.sh --dry-run
```

## Run mock-only checks

```bash
bash scripts/smoke/smoke_e2e_outbound_safety.sh
bash scripts/smoke/smoke_e2e_openclaw_inbound_event.sh
bash scripts/smoke/smoke_e2e_transcript_sync.sh
bash scripts/smoke/smoke_e2e_same_route_reply.sh
bash scripts/smoke/smoke_e2e_unresolved_event_replay.sh
bash scripts/smoke/smoke_e2e_attachment_persist.sh
```

## Live API checks

`smoke_e2e_integration_task.sh` and `smoke_e2e_runtime_health.sh` can call a NexusDesk API. Do not run against production unless you explicitly accept test-data creation.

```bash
bash scripts/smoke/smoke_e2e_runtime_health.sh --api-url http://127.0.0.1:18081
```

## Result semantics

- `PASS`: the script proved the intended mock or live assertion.
- `SKIP`: required live credentials or test database are missing.
- `FAIL`: code, fixture, service, or environment behavior did not match the expected contract.

## Production warning

Do not use live OpenClaw mode on production customer accounts during Round A. Round A is intended for mock mode, CI mode, or a disposable staging environment.
