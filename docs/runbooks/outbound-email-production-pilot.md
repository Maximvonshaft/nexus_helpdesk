# Outbound Email Production Pilot Runbook

## Scope

This runbook covers the first bounded production pilot for `channel=email` through SMTP. It does not authorize a broad rollout. Attachments, templates, bounce processing, delivery receipts and alternate Provider APIs remain outside this pilot unless separately qualified.

The pilot is a capability activation on top of an already healthy controlled deployment. It is not a second deployment procedure.

## Preconditions

- The exact source and immutable image passed Canonical Acceptance and controlled server preflight.
- `frontend_dist` and backend runtime report the same source identity.
- PostgreSQL is at the expected Alembic head.
- `app-controlled` and `worker-outbound-controlled` are healthy.
- A verified rollback image/environment and current database/upload backups exist.
- `OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE` points to a mounted Fernet key file with restricted permissions.
- The signed activation evidence contains a passing outbound Email entry bound to the exact source, image, configuration digest and environment ID.

Example key installation on the target server:

```bash
install -m 0600 -o nexus -g nexus \
  /secure/source/outbound_email_encryption_key \
  /run/nexus/outbound_email_encryption_key
```

## Fail-closed defaults

Keep these values until the controlled test-send passes:

```text
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=false
OUTBOUND_EMAIL_TEST_SEND_MAX_AGE_HOURS=24
```

Do not enable SMTP dispatch merely because an account can be saved in the UI.

## Configure the SMTP account

1. Log in with `channel_account.manage`.
2. Open `/outbound-email`.
3. Create and test the global fallback account before adding market-specific accounts.
4. Use `STARTTLS` or `SSL/TLS` unless the mail platform explicitly requires a private-network exception.
5. Confirm the UI exposes only configured/masked password state, never plaintext.

## Browser smoke

Run against the deployed controlled URL:

```bash
cd webapp
PLAYWRIGHT_BASE_URL=https://support.example.com \
NEXUS_REAL_ADMIN_SMOKE=1 \
NEXUS_ADMIN_USERNAME=admin@example.com \
NEXUS_ADMIN_PASSWORD='...' \
npx playwright test e2e/outbound-email-admin-real.spec.ts
```

Expected result: authentication succeeds, the SMTP configuration page renders, and the test-send controls are available to the authorized operator.

## Real test-send gate

Use a controlled non-customer recipient:

```bash
NEXUS_BASE_URL=https://support.example.com \
NEXUS_ADMIN_USERNAME=admin@example.com \
NEXUS_ADMIN_PASSWORD='...' \
OUTBOUND_EMAIL_TEST_TO=ops-smoke@example.com \
OUTBOUND_EMAIL_TEST_SEND_CONFIRM=I_UNDERSTAND_THIS_SENDS_REAL_EMAIL \
python scripts/smoke/outbound_email_test_send_gate.py
```

Optional: set `OUTBOUND_EMAIL_ACCOUNT_ID` to force a reviewed account.

Pass criteria:

- API result is successful;
- Provider status is successful;
- account health persists `ok` with a fresh successful test timestamp;
- the controlled recipient receives the message;
- no customer address was used;
- production readiness recognizes a fresh successful SMTP account.

## Activate the bounded pilot

After the test-send and signed evidence are complete, set the reviewed production activation values:

```text
ENABLE_OUTBOUND_DISPATCH=true
OUTBOUND_PROVIDER=email
OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=true
OUTBOUND_PRODUCTION_E2E_EVIDENCE_URL=<signed-manifest-matching-https-url>
```

Re-render and validate the complete controlled plus activation environment:

```bash
python scripts/deploy/validate_production_activation.py \
  --env-file deploy/.env.controlled \
  --env-file deploy/.env.production-activation \
  --output /tmp/nexus-email-pilot-activation.json

docker compose \
  --env-file deploy/.env.controlled \
  --env-file deploy/.env.production-activation \
  -f deploy/docker-compose.controlled.yml \
  -f deploy/docker-compose.production-activation.yml \
  up -d --no-build --pull always \
  production-activation-preflight \
  app-controlled \
  worker-outbound-controlled
```

The activation preflight must complete successfully before the application and outbound Worker are accepted as active.

## Pilot send

Begin with one reviewed test Case in one approved market/team. Before sending, verify:

- channel is Email;
- recipient and subject are explicitly reviewed;
- body contains no unsupported claims, credentials or internal details;
- external SMTP confirmation is intentional;
- the outbox row progresses from pending/processing to Provider-confirmed sent or an explicit failure/review state;
- the controlled recipient actually receives the message.

API acceptance alone is not delivery evidence.

## Immediate stop and rollback

To stop new external Email dispatch, restore the master and capability kill switches in the reviewed activation environment:

```text
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=false
```

Validate and reapply the controlled posture to `app-controlled` and `worker-outbound-controlled`. Existing pending rows must remain undispatched while the master switch is off.

If the image itself must be rolled back, use `scripts/deploy/rollback_release.sh` with the previous immutable digest and its matching controlled environment. Do not rebuild an old checkout on the production server.

Finally disable the affected SMTP account and preserve Provider responses, outbox state and customer-impact evidence for review.
