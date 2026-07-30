# Production Runbook

## Authority

This file is the production navigation entrypoint. It does not define a second release procedure.

The exact-candidate qualification and deployment authorities are:

- `docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md`;
- `python scripts/verify_repository.py`;
- `scripts/deploy/validate_controlled_server_preflight.py`;
- `docs/runbooks/production-activation.md`;
- `deploy/docker-compose.controlled.yml`;
- optional local database overlay: `deploy/docker-compose.controlled-postgres.yml`;
- canonical wrapper: `deploy/nexus-prod-compose.sh`.

Historical shared environment injection, mutable image tags and retired server/candidate Compose files are not release authorities.

## Required posture

Before controlled deployment:

1. freeze one exact source Head and clean tree;
2. complete the exact-head acceptance without changing that Head;
3. build and verify one immutable image digest;
4. keep SBOM, provenance, signature and verification evidence outside the repository;
5. prepare one regular, non-symlink controlled environment file for the selected database topology;
6. run the controlled preflight against the exact manifest;
7. preserve production-local configuration, PostgreSQL and uploads before cutover;
8. bind the rollback plan to the previous immutable image and matching environment.

No individual step authorizes Provider or customer-facing traffic.

## Canonical configuration rendering

External PostgreSQL:

```bash
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
deploy/nexus-prod-compose.sh config --quiet
```

Local PostgreSQL:

```bash
NEXUS_DATABASE_TOPOLOGY=local \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled.local-postgres \
deploy/nexus-prod-compose.sh config --quiet
```

The wrapper requires an explicit topology. It must not infer or silently reuse a historical environment file.

## Safety defaults

The first controlled deployment remains fail closed:

```text
PROVIDER_RUNTIME_ENABLED=false
PROVIDER_RUNTIME_TRAFFIC_MODE=control
PROVIDER_RUNTIME_KILL_SWITCH=true
PROVIDER_RUNTIME_CANARY_PERCENT=0
WEBCHAT_AI_ENABLED=false
WEBCHAT_HUMAN_CALL_ENABLED=false
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
WHATSAPP_ENABLED=false
WHATSAPP_EMBEDDED_SIGNUP_ENABLED=false
WHATSAPP_MEDIA_ENABLED=false
EMAIL_MAILBOX_SYNC_ENABLED=false
SPEEDAF_MCP_ENABLED=false
OPERATIONS_DISPATCH_MODE=disabled
```

Disabled capabilities receive no Provider, AI, Voice or channel credential.

## Runtime verification

After starting the controlled topology, run:

```bash
APP_DIR=/opt/nexus_helpdesk \
APP_URL=http://127.0.0.1:18095 \
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
bash scripts/probe_nexus_runtime.sh
```

The probe is the single runtime verification entrypoint. Production activation and capability-specific E2E evidence are governed by `docs/runbooks/production-activation.md`.

## Backup and rollback

- `scripts/deploy/safe_update_server.sh` creates a private checksum-verified copy of controlled local configuration; it does not deploy.
- Database restore qualification is owned by `scripts/qualification/recovery/run_recovery_qualification.sh`.
- Image rollback through `scripts/deploy/rollback_release.sh` requires:
  - immutable prior `OLD_IMAGE_TAG` digest;
  - `ROLLBACK_CONTROLLED_ENV_FILE` whose image, source, frontend and migration identity match that prior release;
  - explicit `ROLLBACK_DATABASE_TOPOLOGY=external|local`;
  - health verification after restart.

Never overwrite production-local environment files, database volumes, uploads, backups, secrets or server-only overrides merely because repository templates changed. Never run an unbounded Docker prune.
