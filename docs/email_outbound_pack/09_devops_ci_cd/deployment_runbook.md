# Deployment Runbook

## Pre-deploy

1. Merge code with Email disabled by default.
2. Apply migration in staging.
3. Configure staging SES identity.
4. Configure webhook secret.
5. Confirm DNS/domain verification.
6. Enable staging flags only after tests.

## Deploy sequence

```bash
git fetch --all
git checkout <release-branch>
docker compose build app worker
docker compose run --rm app alembic upgrade head
docker compose up -d app worker
```

## Staging enablement

```bash
OUTBOUND_EMAIL_ENABLED=true
EMAIL_PROVIDER=ses
EMAIL_DELIVERY_EVENTS_ENABLED=true
EMAIL_INBOUND_ENABLED=true
```

Keep production disabled until staging smoke passes.

## Post-deploy checks

- Healthz/readyz.
- Capability API.
- Email smoke send.
- Worker logs.
- Provider dashboard.
- Delivery event webhook.
- Inbound reply parser.
