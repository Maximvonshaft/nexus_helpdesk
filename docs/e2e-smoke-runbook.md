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

## Webapp Playwright smoke

The authenticated console now has a minimal Playwright smoke suite under `webapp/e2e/`.
It uses mocked `/api/**` responses and `sessionStorage` token fixtures, so it does **not** require a real admin account or production credentials.

Run locally:

```bash
cd webapp
npm ci
npm run build
npm run e2e
```

Current smoke coverage:

- login page renders
- unauthenticated protected route redirects to `/login`
- agent navigation does not expose management entry points
- admin/capability user navigation exposes management entry points
- admin/capability user can open `/outbound-email` with masked SMTP account state

CI note:

- `webapp-build` runs this mock-safe suite and uploads the Playwright report.

## Real admin Outbound Email smoke

The real admin smoke does not mock `/api/**`. It is skipped unless explicitly enabled and should run only against staging or a controlled production pilot target.

```bash
cd webapp
PLAYWRIGHT_BASE_URL=https://support.example.com \
NEXUS_REAL_ADMIN_SMOKE=1 \
NEXUS_ADMIN_USERNAME=admin@example.com \
NEXUS_ADMIN_PASSWORD='...' \
npx playwright test e2e/outbound-email-admin-real.spec.ts
```

This smoke verifies login, `/outbound-email` route access, and visibility of the SMTP test-send controls. It does not send email.

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

Outbound Email production pilot test-send is covered separately in `docs/runbooks/outbound-email-production-pilot.md` because it intentionally sends a real email.
