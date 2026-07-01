# NexusDesk E2E Smoke Runbook

## Purpose

These smoke checks validate the current NexusDesk runtime without depending on the retired ExternalChannel stack. They focus on WebChat, Provider Runtime, outbound safety, Speedaf contracts, worker readiness, and browser bundle hygiene.

## Default Safety Model

- Default mode is mock or dry-run safe.
- Scripts must not send real WhatsApp, Telegram, SMS, or customer messages unless explicitly documented.
- Missing live credentials should result in `SKIP`, not fake `PASS`.
- Production smoke should run against a candidate path before traffic cutover.

## Common Environment

```bash
export NEXUSDESK_API_URL=http://127.0.0.1:18081
export NEXUSDESK_SMOKE_MODE=mock
export NEXUSDESK_SMOKE_PREFIX=nexus-local
export NEXUSDESK_EXTERNAL_SEND_MOCK_MODE=1
```

Optional live integration variables:

```bash
export NEXUSDESK_INTEGRATION_CLIENT_ID=...
export NEXUSDESK_INTEGRATION_CLIENT_KEY=...
export NEXUSDESK_ADMIN_EMAIL=...
export NEXUSDESK_ADMIN_PASSWORD=...
```

## Main Smoke Entrypoints

```bash
bash scripts/smoke/smoke_all_round_a.sh --dry-run
bash scripts/smoke/smoke_e2e_runtime_health.sh --api-url "$NEXUSDESK_API_URL"
bash scripts/smoke/smoke_e2e_outbound_safety.sh
bash scripts/smoke/smoke_webchat_round_b.sh
bash scripts/smoke/smoke_webchat_ai_runtime.sh
bash scripts/smoke/webchat_fast_reply_release_gate.sh
python scripts/smoke/browser_bundle_secret_scan.py
python scripts/smoke/worker_daemon_readiness_probe.py --compose-file deploy/docker-compose.server.yml
```

## Webapp Playwright Smoke

The authenticated console has Playwright smoke coverage under `webapp/e2e/`. It uses mocked `/api/**` responses and `sessionStorage` token fixtures, so it does not require a real admin account or production credentials.

```bash
cd webapp
npm ci
npm run build
npm run e2e
```

Current coverage includes login routing, role/capability navigation, outbound email controls, runtime pages, and WebChat-inbox surfaces.

## Real Admin Smoke

The real admin smoke does not mock `/api/**`. Run it only against staging or a controlled pilot target.

```bash
cd webapp
PLAYWRIGHT_BASE_URL=https://support.example.com \
NEXUS_REAL_ADMIN_SMOKE=1 \
NEXUS_ADMIN_USERNAME=admin@example.com \
NEXUS_ADMIN_PASSWORD='...' \
npx playwright test e2e/outbound-email-admin-real.spec.ts
```

This smoke verifies login, `/outbound-email` route access, and SMTP test-send controls. It does not send email unless the test-send gate is explicitly invoked.

## Result Semantics

- `PASS`: the script proved the intended assertion.
- `SKIP`: required live credentials or target services are missing.
- `FAIL`: code, fixture, service, or environment behavior did not match the expected contract.

## Production Warning

Do not run live outbound or provider tests against production customer accounts without an approved test window, candidate path, and rollback plan.
