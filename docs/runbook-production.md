# Production Runbook

## Authority

This file is a navigation entrypoint, not a second release procedure.

The sole exact-candidate qualification sequence is:

- `docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md`
- `python scripts/verify_repository.py`
- `scripts/deploy/validate_controlled_server_preflight.py`
- `deploy/docker-compose.controlled.yml`
- optional local database overlay: `deploy/docker-compose.controlled-postgres.yml`

GitHub Actions, `.env.prod`, a mutable image tag, `docker-compose.server.yml`
service definitions and candidate-specific sidecars are not release authorities.

## Required posture

Before any controlled deployment is considered:

1. Freeze one exact source Head and clean tree.
2. Complete the exact-head runbook without changing that Head.
3. Build and verify one immutable image Digest.
4. Keep SBOM, provenance, signature and verification evidence outside the repository.
5. Prepare one regular, non-symlink controlled environment file:
   - external PostgreSQL: `deploy/.env.controlled` from `.env.controlled.example`;
   - local PostgreSQL: `deploy/.env.controlled.local-postgres` from `.env.controlled.local-postgres.example`.
6. Run the v2 controlled preflight against the exact manifest.
7. Preserve the production-local configuration and data before any cutover.
8. Obtain an independent Review bound to the same final Head.

No step above authorizes a deployment by itself.

## Canonical commands

External PostgreSQL configuration rendering:

```bash
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
deploy/nexus-prod-compose.sh config --quiet
```

Local PostgreSQL configuration rendering:

```bash
NEXUS_DATABASE_TOPOLOGY=local \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled.local-postgres \
deploy/nexus-prod-compose.sh config --quiet
```

The wrapper requires an explicit topology. It must not infer or silently reuse a
historical `.env.prod` file.

## Safety defaults

The first controlled profile remains fail closed:

```text
PROVIDER_RUNTIME_ENABLED=false
PROVIDER_RUNTIME_TRAFFIC_MODE=control
PROVIDER_RUNTIME_KILL_SWITCH=true
PROVIDER_RUNTIME_CANARY_PERCENT=0
WEBCHAT_AI_ENABLED=false
WEBCHAT_VOICE_ENABLED=false
WEBCHAT_HUMAN_CALL_ENABLED=false
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
WHATSAPP_NATIVE_ENABLED=false
WHATSAPP_DISPATCH_MODE=disabled
EMAIL_MAILBOX_SYNC_ENABLED=false
OPERATIONS_DISPATCH_MODE=disabled
```

Disabled capabilities receive no Provider, AI, voice or WhatsApp credential.

## Backup and rollback

- `scripts/deploy/safe_update_server.sh` creates a private checksum-verified copy
  of historical and controlled local configuration; it does not deploy.
- Database restore qualification remains under
  `scripts/qualification/recovery/run_recovery_qualification.sh`.
- Image rollback through `scripts/deploy/rollback_release.sh` requires:
  - immutable `OLD_IMAGE_TAG` Digest;
  - `ROLLBACK_CONTROLLED_ENV_FILE` whose image/source/migration identity matches
    that prior release;
  - explicit `ROLLBACK_DATABASE_TOPOLOGY=external|local`;
  - health verification after restart.

Never overwrite production-local files merely because repository templates
changed. Never run an unbounded Docker prune.
