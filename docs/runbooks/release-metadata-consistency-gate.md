# Release Metadata Consistency Gate

## Purpose

This gate prevents a production release from passing when Docker is running one image but `/healthz` or `/readyz` reports a different `image_tag`.

This failure previously occurred because the app-family containers were recreated with the correct image while stale metadata environment variables remained in the runtime override.

## Scope

This gate is read-only.

It checks:

1. `docker inspect deploy-app-1 .Config.Image == /healthz image_tag`
2. `/healthz image_tag == /readyz image_tag`
3. `/readyz database == ok`
4. `/readyz migration_revision` is non-empty

The gate writes evidence files and exits non-zero on failure.

## Command

```bash
PYTHONPATH=backend python3 scripts/release_metadata_consistency_gate.py \
  --container deploy-app-1 \
  --base-url http://127.0.0.1 \
  --evidence-dir forensics/release_metadata_consistency_gate_$(date -u +%Y%m%dT%H%M%SZ)
```

## Evidence contract

The gate writes:

- `docker_image_truth.json`
- `healthz_payload.json`
- `readyz_payload.json`
- `final_assertion_result.json`
- `final_assertion_result.txt`

## Dry-run mode

Dry-run mode does not require Docker or network access:

```bash id="4oby7w"
PYTHONPATH=backend python3 scripts/release_metadata_consistency_gate.py \
  --docker-image nexusdesk/helpdesk:example \
  --healthz-file /tmp/healthz.json \
  --readyz-file /tmp/readyz.json \
  --evidence-dir /tmp/release-metadata-gate
```

## Production baseline

Validated stable baseline:

- Runtime image: `nexusdesk/helpdesk:main-cf13200-support-hours-policy-v2-20260521T130459Z`
- Stable baseline archive: `/root/nexus_stable_baseline_freeze_20260521T133804Z.tar.gz`

## Non-goals

This gate must not modify:

- Webchat Fast Reply behavior
- Handoff or ticket creation behavior
- Database schema
- Frontend UI behavior
- Docker Compose service topology
- `sync-daemon` state
