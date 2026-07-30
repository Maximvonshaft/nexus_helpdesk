# Release Metadata Consistency Gate

## Purpose

This read-only gate prevents a release from passing when Docker is running one immutable image while `/healthz` or `/readyz` reports another identity.

It checks:

1. the explicitly selected application container image equals `/healthz.image_tag`;
2. `/healthz.image_tag` equals `/readyz.image_tag`;
3. `/readyz.database` is `ok`;
4. `/readyz.migration_revision` is non-empty;
5. optionally, both endpoints report complete release metadata.

The gate writes evidence files and exits non-zero on failure.

## Controlled command

Resolve the application container through the canonical wrapper. Do not guess a Docker project/container name.

```bash
container="$(
  NEXUS_DATABASE_TOPOLOGY=external \
  NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
  deploy/nexus-prod-compose.sh ps -q app-controlled
)"
test -n "$container"

PYTHONPATH=backend python3 scripts/release_metadata_consistency_gate.py \
  --container "$container" \
  --base-url http://127.0.0.1:18095 \
  --require-complete-metadata \
  --evidence-dir "/tmp/nexus-release-metadata-$(date -u +%Y%m%dT%H%M%SZ)"
```

Use the selected local database topology and matching controlled environment when that is the actual deployment.

## Evidence contract

The gate writes:

- `docker_image_truth.json`;
- `healthz_payload.json`;
- `readyz_payload.json`;
- `final_assertion_result.json`;
- `final_assertion_result.txt`.

## Offline evaluation

Offline mode does not require Docker or network access:

```bash
PYTHONPATH=backend python3 scripts/release_metadata_consistency_gate.py \
  --docker-image ghcr.io/example/nexus@sha256:<digest> \
  --healthz-file /tmp/healthz.json \
  --readyz-file /tmp/readyz.json \
  --require-complete-metadata \
  --evidence-dir /tmp/release-metadata-gate
```

The image argument must be the exact image identity represented in the supplied endpoint payloads.

## Non-goals

This gate does not modify:

- WebChat behavior;
- Handoff or Ticket state;
- database schema;
- frontend assets;
- Compose topology;
- `worker-outbound-controlled`;
- `worker-background-controlled`;
- `worker-webchat-ai-controlled`.
