# Nexus production activation

## Decision model

Nexus has one deployment authority with three profiles:

| Profile | Purpose | External effects |
| --- | --- | --- |
| `controlled` | Prove image, database, migrations, storage, queues and web health | Disabled |
| `provider_canary` | Exercise a bounded model Provider canary | Provider only, 1–25% |
| `full` | Authorize customer-facing production capabilities | Only capabilities with real E2E evidence |

A green pull-request workflow proves the immutable software candidate. It does not prove the target server, Provider account, carrier, DID, SMTP account or customer-facing network path. Those facts are admitted through the production activation gate after the controlled deployment is healthy.

## 1. Deploy the controlled candidate

Use the immutable image digest and current single Alembic head from the accepted manifest.

```bash
docker compose \
  --env-file deploy/.env.controlled \
  -f deploy/docker-compose.controlled.yml \
  up -d migrate-controlled app-controlled \
    worker-background-controlled worker-webchat-ai-controlled \
    worker-handoff-snapshot-controlled
```

The controlled environment must keep Provider, WebChat AI, Voice, outbound and Operations effects disabled. `/readyz` must report the exact source SHA, frontend SHA, image digest and migration revision.

## 2. Produce real E2E evidence

Evidence URLs must use HTTPS and point to the actual report or evidence bundle for the same source SHA and image digest.

Required evidence by profile or capability:

- `PROVIDER_CANARY_E2E_EVIDENCE_URL` for `provider_canary`.
- `PRODUCTION_E2E_EVIDENCE_URL` for every `full` activation.
- `WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL` when automatic WebChat AI is enabled.
- `TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL` when human or AI Voice is enabled.
- `OUTBOUND_PRODUCTION_E2E_EVIDENCE_URL` when outbound dispatch is enabled.
- `OPERATIONS_PRODUCTION_E2E_EVIDENCE_URL` when Operations dispatch is enabled.

A placeholder, plain HTTP URL or missing capability-specific evidence fails closed.

## 3. Validate the activation environment

Create `deploy/.env.production-activation` from the example and run:

```bash
python scripts/deploy/validate_production_activation.py \
  --env-file deploy/.env.controlled \
  --env-file deploy/.env.production-activation \
  --output /tmp/nexus-production-activation-preflight.json
```

This command performs no external effects and emits no secret values. It validates the exact rollout profile, Provider mode, kill switch, canary percentage, enabled capability dependencies and evidence URLs.

## 4. Apply the activation overlay

```bash
docker compose \
  --env-file deploy/.env.controlled \
  --env-file deploy/.env.production-activation \
  -f deploy/docker-compose.controlled.yml \
  -f deploy/docker-compose.production-activation.yml \
  up -d
```

Start the `telephony` profile only when the real LiveKit/SIP/STT/TTS prerequisites and telephony E2E evidence are present.

## 5. Verify the live authority

Run the in-container preflight:

```bash
docker compose \
  --env-file deploy/.env.controlled \
  --env-file deploy/.env.production-activation \
  -f deploy/docker-compose.controlled.yml \
  -f deploy/docker-compose.production-activation.yml \
  exec app-controlled python scripts/validate_production_readiness.py
```

In the operator product, open **系统运行 → 上线与激活**. The page evaluates:

- immutable runtime identity;
- exact Alembic head;
- Provider and rollout controls;
- queue health;
- local-storage backup equality or remote storage;
- telephony configuration and channel readiness;
- required activation evidence;
- database pool state.

`production_authorized=true` is emitted only for the `full` profile when every collector passes. Capability-specific authorization remains false unless that capability is enabled, configured and backed by its own E2E evidence.

## 6. Rollback

Keep the previous immutable image digest and environment snapshot. If runtime health, customer outcomes or Provider behavior degrade:

1. Restore `PROVIDER_RUNTIME_KILL_SWITCH=true`.
2. Disable the affected capability flags.
3. Reapply the controlled Compose profile.
4. Roll back the immutable image using the canonical rollback script.
5. Preserve the failure evidence and audit trail before retrying activation.

No UI action, environment switch or model response can override a failed production activation collector.
