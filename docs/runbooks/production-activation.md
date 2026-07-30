# Nexus production activation

## Decision model

Nexus has one deployment authority with three profiles:

| Profile | Purpose | External effects |
| --- | --- | --- |
| `controlled` | Prove image, database, migrations, storage, queues and web health | Disabled |
| `provider_canary` | Exercise a bounded model Provider canary | Provider only, 1–25% |
| `full` | Authorize customer-facing production capabilities | Only capabilities with real E2E evidence |

A green repository acceptance proves the software candidate. It does not prove the target server, Provider account, carrier, DID, SMTP account, GPU runtime or customer-facing network path. Those facts are admitted only after the controlled deployment is healthy.

## 1. Prepare the controlled environment

Choose one explicit database topology and create the matching untracked environment file:

- external PostgreSQL: `deploy/.env.controlled`;
- local PostgreSQL: `deploy/.env.controlled.local-postgres`.

The environment must bind an immutable image digest, exact source/frontend SHA, build time, application version and the single expected Alembic head. All Provider, AI, Voice, outbound and Operations effects remain disabled.

Render before mutation:

```bash
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
deploy/nexus-prod-compose.sh config --quiet
```

Use `NEXUS_DATABASE_TOPOLOGY=local` and the local PostgreSQL environment file only when the server intentionally owns the database container.

## 2. Deploy the controlled candidate

```bash
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
deploy/nexus-prod-compose.sh up -d --no-build --pull always \
  migrate-controlled \
  app-controlled \
  worker-outbound-controlled \
  worker-background-controlled \
  worker-webchat-ai-controlled
```

The background Worker owns authoritative background work, including Handoff snapshot projection. There is no separate deployment service for that projection.

After the migration role exits successfully, verify the long-running services:

```bash
APP_DIR=/opt/nexus_helpdesk \
APP_URL=http://127.0.0.1:18095 \
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
bash scripts/probe_nexus_runtime.sh
```

The probe must prove:

- `/healthz` and `/readyz` return HTTP 200;
- source, frontend, image and migration identity are complete;
- upload storage is writable and its backup contract is ready;
- all three supervised Workers report fresh durable progress;
- the Metrics endpoint rejects unauthenticated access and succeeds with its configured token;
- no unexpected external outbound backlog exists.

## 3. Produce real E2E evidence

Evidence URLs must use HTTPS and point to the actual report or evidence bundle for the same source SHA and image digest.

Required evidence by profile or capability:

- `PROVIDER_CANARY_E2E_EVIDENCE_URL` for `provider_canary`;
- `PRODUCTION_E2E_EVIDENCE_URL` for every `full` activation;
- `WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL` when automatic WebChat AI is enabled;
- `TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL` when human or AI Voice is enabled;
- `OUTBOUND_PRODUCTION_E2E_EVIDENCE_URL` when outbound dispatch is enabled;
- `OPERATIONS_PRODUCTION_E2E_EVIDENCE_URL` when Operations dispatch is enabled.

The activation environment also binds all evidence to:

- `ACTIVATION_EVIDENCE_SOURCE_SHA`, exactly equal to `GIT_SHA`;
- `ACTIVATION_EVIDENCE_IMAGE_DIGEST`, exactly equal to the digest suffix of `CONTROLLED_IMAGE`.

A placeholder, plain HTTP URL, wrong candidate binding or missing capability-specific evidence fails closed.

## 4. Validate the activation environment

Create `deploy/.env.production-activation` from the example and run:

```bash
python scripts/deploy/validate_production_activation.py \
  --env-file deploy/.env.controlled \
  --env-file deploy/.env.production-activation \
  --output /tmp/nexus-production-activation-preflight.json
```

This validation performs no external effects and emits no secret values. It checks the rollout profile, immutable identity, Provider mode, kill switch, canary percentage, capability dependencies and signed evidence.

## 5. Apply the activation overlay

```bash
docker compose \
  --env-file deploy/.env.controlled \
  --env-file deploy/.env.production-activation \
  -f deploy/docker-compose.controlled.yml \
  -f deploy/docker-compose.production-activation.yml \
  up -d --no-build --pull always
```

The overlay starts the isolated, networkless `production-activation-preflight` container first. The application and affected Workers depend on its successful completion. Invalid controls, candidate binding, Provider prerequisites or evidence prevent activation.

Start the `telephony` profile only when the real LiveKit/SIP/STT/TTS prerequisites and telephony E2E evidence are present.

## 6. Verify the live authority

```bash
docker compose \
  --env-file deploy/.env.controlled \
  --env-file deploy/.env.production-activation \
  -f deploy/docker-compose.controlled.yml \
  -f deploy/docker-compose.production-activation.yml \
  exec -T app-controlled python scripts/validate_production_readiness.py
```

The live authority evaluates immutable runtime identity, Alembic head, Provider controls, queue health, storage backup, telephony/channel readiness, activation evidence and database pool state.

`production_authorized=true` is emitted only for the `full` profile when every collector passes. Capability-specific authorization remains false unless that capability is enabled, configured and backed by its own real E2E evidence.

## 7. Rollback

Keep the previous immutable image digest, matching controlled environment snapshot and verified database/upload backups. If runtime health, customer outcomes or Provider behavior degrade:

1. restore `PROVIDER_RUNTIME_KILL_SWITCH=true`;
2. disable the affected capability flags;
3. reapply the controlled profile;
4. run `scripts/deploy/rollback_release.sh` with the previous immutable digest and matching environment;
5. preserve failure evidence and the audit trail before retrying activation.

No UI action, environment switch or model response can override a failed production activation collector.
