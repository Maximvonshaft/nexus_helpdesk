# Outbound Email Production Pilot Runbook

## Scope

This runbook covers the first controlled production pilot for `channel=email` outbound sending through SMTP. It assumes the backend registry, SMTP runtime, admin UI, and operator Email subject UX are deployed.

Do not treat this as a broad rollout. Attachments, templates, bounce processing, delivery receipts, and provider API alternatives are outside this pilot.

## Preconditions

- PR-1, PR-2, and PR-3 are deployed from `main`.
- `frontend_dist/index.html` was built from the same release SHA as the backend.
- PostgreSQL migrations are at head.
- Worker process is deployed and healthy.
- `OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE` points to a mounted Fernet key file with restricted permissions, for example:

```bash
install -m 0600 -o nexus -g nexus /secure/source/outbound_email_encryption_key /run/nexus/outbound_email_encryption_key
```

## Safe Env Defaults

Keep these defaults until the test-send gate passes:

```bash
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=false
OUTBOUND_EMAIL_TEST_SEND_MAX_AGE_HOURS=24
```

The current outbound runtime still uses the shared external dispatch kill switch. For the pilot window, only enable real dispatch after the SMTP account has passed a fresh test-send:

```bash
ENABLE_OUTBOUND_DISPATCH=true
OUTBOUND_PROVIDER=openclaw
OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true
```

## Configure SMTP Account

1. Log in as an admin with `channel_account.manage`.
2. Open `/outbound-email`.
3. Create a global fallback SMTP account first. Add market-specific accounts only after global fallback is tested.
4. Use `STARTTLS` or `SSL/TLS` unless the mail platform explicitly requires `plain` inside a controlled private network.
5. Save the account. Confirm the UI shows only password configured/masked state, not plaintext.

## Browser Smoke

Use the manual staging smoke workflow with `check_outbound_email_admin=true`, or run locally against a deployed URL:

```bash
cd webapp
PLAYWRIGHT_BASE_URL=https://support.example.com \
NEXUS_REAL_ADMIN_SMOKE=1 \
NEXUS_ADMIN_USERNAME=admin@example.com \
NEXUS_ADMIN_PASSWORD='...' \
npx playwright test e2e/outbound-email-admin-real.spec.ts
```

Expected result: admin login succeeds, `/outbound-email` renders `SMTP 账号配置`, and the test-send controls are visible.

## Test-Send Gate

The gate sends a real email to the controlled test recipient. Do not use a customer address.

```bash
NEXUS_BASE_URL=https://support.example.com \
NEXUS_ADMIN_USERNAME=admin@example.com \
NEXUS_ADMIN_PASSWORD='...' \
OUTBOUND_EMAIL_TEST_TO=ops-smoke@example.com \
OUTBOUND_EMAIL_TEST_SEND_CONFIRM=I_UNDERSTAND_THIS_SENDS_REAL_EMAIL \
python scripts/smoke/outbound_email_test_send_gate.py
```

Optional: set `OUTBOUND_EMAIL_ACCOUNT_ID=123` to force a specific account. Otherwise the gate uses the first active account with a configured password.

Pass criteria:

- API returns `ok=true`.
- `provider_status` is successful.
- The account persists `health_status=ok`, `last_test_status=success`, and a fresh `last_test_at`.
- The production readiness endpoint reports at least one successful Outbound Email test-send account when `OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true`.

## Pilot Send

After the gate passes, enable the pilot flags and restart `app` plus `worker`. Limit the pilot to a known market/team and one test customer ticket first.

Operator checklist before clicking send:

- Channel is `Email`.
- Recipient is the expected customer email.
- Subject is explicit and reviewed.
- Body contains no unsupported claims, credentials, or internal details.
- External SMTP confirmation checkbox is checked intentionally.

## Rollback

To stop real Email dispatch immediately:

```bash
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=false
docker compose -f deploy/docker-compose.cloud.yml up -d app worker
```

Then disable the SMTP account in `/outbound-email`. Existing pending Email outbox rows will not be dispatched while the kill switch is off.
