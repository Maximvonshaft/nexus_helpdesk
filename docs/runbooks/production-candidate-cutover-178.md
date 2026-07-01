# 178 Candidate Cutover And Rollback Runbook

This runbook is for a controlled switch after the reconciliation PR is merged and a release image is built. It must not be used to patch production in place.

## Current Baseline Checked On 2026-07-01

- Public `www.leakle.com` was observed on `127.0.0.1:18086`.
- Public `/healthz` reported `git_sha=a355bcb988ff91e3fc30470c1b5377434b0ed5b2`.
- Public image tag was `ghcr.io/maximvonshaft/nexus_helpdesk/helpdesk:candidate-a355bcb988ff-20260701T093515Z`.
- WebChat demo loaded, the `Track parcel` quick reply called `/api/webchat/fast-reply`, and the page rendered an AI reply from `private_ai_runtime`.
- `/opt/nexus_helpdesk` was still an old dirty source tree and must not be treated as runtime truth. Public runtime truth comes from nginx, Docker, and `/healthz`.

## Preconditions

- A GitHub Actions-built image exists for the merge commit.
- Release metadata is known: `GIT_SHA`, `IMAGE_TAG`, `BUILD_TIME`, `APP_VERSION`, `FRONTEND_BUILD_SHA`.
- `deploy/.env.candidate` is created from `deploy/.env.candidate.example` on the server and is not committed.
- nginx runtime values are rendered from `deploy/nginx/nexusdesk.edge.conf.template`; tokens are injected outside the repo.
- Current production config is backed up before any reload.

## Read-Only Drift Audit Before Any Change

Run this before candidate start, nginx cutover, rollback, or cleanup. It records public metadata, loopback upstream ports, Docker inventory, and git worktree drift without dumping token-bearing nginx config.

```bash
cd /opt/nexus_candidate/<release-sha>

BASE_URL=https://www.leakle.com \
EXPECTED_GIT_SHA=<release-sha> \
EXPECTED_IMAGE_TAG=<release-image-tag> \
OUT_DIR="forensics/production_drift_$(date -u +%Y%m%dT%H%M%SZ)" \
scripts/deploy/audit_production_drift.sh
```

Keep the generated `summary.json` with the release evidence. If the public SHA/image does not match the intended release, stop and reconcile routing before changing containers.

## Build Release Image In GitHub Actions

Do not build the release image on 178. Trigger the manual image workflow from the intended release commit and use its `release-metadata` artifact as the source of truth:

```bash
gh workflow run release-image.yml \
  --ref <release-branch-or-sha> \
  -f app_version_prefix=candidate \
  -f push_image=true

gh run watch
gh run download <run-id> -n release-metadata -D /tmp/nexus-release-metadata
cat /tmp/nexus-release-metadata/release-metadata.env
```

The default image target is `ghcr.io/<owner>/<repo>/helpdesk:<app_version>-<build_time>`. If GHCR package visibility is private, log in on 178 with a least-privileged read token before `docker pull`, or make the package public after review.

## Start Candidate

```bash
release_sha="<merged-release-sha>"
candidate_root="/opt/nexus_candidate/${release_sha}"

install -d -m 0755 /opt/nexus_candidate
git clone https://github.com/Maximvonshaft/nexus_helpdesk.git "$candidate_root"
cd "$candidate_root"
git checkout "$release_sha"

install -m 0600 /opt/nexus_helpdesk/deploy/.env.prod deploy/.env.candidate
cat /tmp/nexus-release-metadata/release-metadata.env >> deploy/.env.candidate
printf '\nCANDIDATE_APP_PORT=18082\nCANDIDATE_EXTERNAL_NETWORK=deploy_default\nRELEASE_CANDIDATE=true\n' >> deploy/.env.candidate

set -a
. /tmp/nexus-release-metadata/release-metadata.env
set +a

docker pull "$IMAGE_TAG"

COMPOSE_PROJECT_NAME=nexusdesk_candidate docker compose \
  -f deploy/docker-compose.candidate.yml \
  --env-file deploy/.env.candidate \
  up -d app-candidate

docker compose -p nexusdesk_candidate \
  -f deploy/docker-compose.candidate.yml \
  ps
```

Candidate should listen only on `127.0.0.1:18082`.
On 178, the production database URL currently resolves through Docker DNS, so
candidate also joins `CANDIDATE_EXTERNAL_NETWORK=deploy_default` while keeping a
separate candidate project network. Do not expose the candidate port publicly.

## Prepare Canonical Production Env

After a candidate is proven and before turning it into the canonical production compose input, prepare a new env file instead of editing the live one in place:

```bash
cd "$candidate_root"

PROD_ENV=/opt/nexus_helpdesk/deploy/.env.prod \
OUTPUT_ENV=/opt/nexus_helpdesk/deploy/.env.prod.next \
APP_HOST_PORT_OVERRIDE=18086 \
scripts/deploy/prepare_production_release_env.sh /tmp/nexus-release-metadata/release-metadata.env
```

Review the diff with secret values redacted. The script only upserts non-secret release metadata and optional `APP_HOST_PORT`; it does not run Docker or reload nginx.

## Smoke Candidate

```bash
cd "$candidate_root"

BASE_URL=http://127.0.0.1:18082 \
EXPECTED_IMAGE_TAG="$IMAGE_TAG" \
EXPECTED_GIT_SHA="$GIT_SHA" \
REQUIRE_RELEASE_METADATA_COMPLETE=true \
scripts/smoke/production_candidate_smoke.sh

PYTHONPATH=backend python3 scripts/release_metadata_consistency_gate.py \
  --docker-image "$IMAGE_TAG" \
  --healthz-url http://127.0.0.1:18082/healthz \
  --readyz-url http://127.0.0.1:18082/readyz \
  --require-complete-metadata \
  --evidence-dir "forensics/candidate_release_metadata_$(date -u +%Y%m%dT%H%M%SZ)"
```

Required pass conditions:

- `/healthz.status == ok`.
- `/readyz.status == ready`.
- `/healthz` and `/readyz` expose complete release metadata.
- `image_tag` and `git_sha` match the intended release.
- Demo page contains `data-live-voice-mode="edge-card"`.
- `voice-entry.js` does not contain production-only upstream/debug markers.
- CORS allows approved origins and rejects a blocked origin.

## Prepare Nginx Candidate Config

```bash
cd "$candidate_root"
cp /etc/nginx/sites-enabled/nexusdesk "/etc/nginx/sites-enabled/nexusdesk.backup.$(date -u +%Y%m%dT%H%M%SZ)"

set -a
. deploy/nginx/nexusdesk.edge.runtime.env
set +a

envsubst < deploy/nginx/nexusdesk.edge.conf.template > /tmp/nexusdesk.candidate.conf
```

Before cutover, edit the runtime env so `NEXUSDESK_APP_UPSTREAM=http://127.0.0.1:18082` in the rendered candidate config. Keep the current production config untouched until the cutover step. The effective `nginx -t` happens after installing the candidate file and before reload; if it fails, restore the backup and do not reload.

## Cutover

```bash
install -m 0644 /tmp/nexusdesk.candidate.conf /etc/nginx/sites-enabled/nexusdesk
if ! nginx -t; then
  latest_backup="$(ls -1t /etc/nginx/sites-enabled/nexusdesk.backup.* | head -n 1)"
  install -m 0644 "$latest_backup" /etc/nginx/sites-enabled/nexusdesk
  nginx -t
  exit 1
fi
systemctl reload nginx

curl -fsS http://127.0.0.1/healthz
curl -fsS http://127.0.0.1/readyz
```

Run a public read-only smoke after reload:

```bash
BASE_URL=http://127.0.0.1 \
EXPECTED_IMAGE_TAG="$IMAGE_TAG" \
EXPECTED_GIT_SHA="$GIT_SHA" \
REQUIRE_RELEASE_METADATA_COMPLETE=true \
scripts/smoke/production_candidate_smoke.sh

BASE_URL=https://www.leakle.com \
EXPECTED_IMAGE_TAG="$IMAGE_TAG" \
EXPECTED_GIT_SHA="$GIT_SHA" \
REQUIRE_AI_REPLY=true \
WEBCHAT_FAST_REPLY_MAX_LATENCY_MS=25000 \
python3 scripts/smoke/public_webchat_smoke.py
```

Also run the GitHub manual smoke so the public result is attached to Actions:

```bash
gh workflow run public-production-smoke.yml \
  --ref main \
  -f base_url=https://www.leakle.com \
  -f expected_git_sha="$GIT_SHA" \
  -f expected_image_tag="$IMAGE_TAG" \
  -f origin=https://www.leakle.com \
  -f require_ai_reply=true \
  -f max_latency_ms=25000 \
  -f skip_fast_reply=false
```

## Rollback

Use rollback on any failed smoke, user-visible regression, `readyz` failure, or nginx error spike.

```bash
latest_backup="$(ls -1t /etc/nginx/sites-enabled/nexusdesk.backup.* | head -n 1)"
install -m 0644 "$latest_backup" /etc/nginx/sites-enabled/nexusdesk
nginx -t
systemctl reload nginx

curl -fsS http://127.0.0.1/healthz
curl -fsS http://127.0.0.1/readyz
```

Candidate can remain running for investigation, or be stopped after rollback:

```bash
COMPOSE_PROJECT_NAME=nexusdesk_candidate docker compose \
  -f deploy/docker-compose.candidate.yml \
  --env-file deploy/.env.candidate \
  down
```

## Cleanup After Stable Window

Only clean up after the public smoke, operator smoke, and rollback drill have passed for the agreed window.

1. Keep the latest rollback candidate and nginx backup.
2. Stop older candidate compose projects that are not the rollback target.
3. Archive `/opt/nexus_helpdesk` dirty drift into a dated tarball before changing or deleting it.
4. Replace the dirty canonical source tree with a clean checkout only after the release owner confirms the archive and rollback path.
5. Run `scripts/deploy/audit_production_drift.sh` again and attach the evidence.

## Evidence To Attach To PR Or Release

- GitHub Actions run URL.
- Release image digest and `IMAGE_TAG`.
- Candidate smoke output and evidence directory.
- Public production smoke Actions URL and evidence artifact.
- Drift audit `summary.json` before and after cutover.
- Release metadata consistency gate output.
- Rendered nginx diff against backup with secrets redacted.
- Final `/healthz` and `/readyz` payloads after cutover or rollback.
