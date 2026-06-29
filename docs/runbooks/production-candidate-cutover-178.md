# 178 Candidate Cutover And Rollback Runbook

This runbook is for a controlled switch after the reconciliation PR is merged and a release image is built. It must not be used to patch production in place.

## Preconditions

- A GitHub Actions-built image exists for the merge commit.
- Release metadata is known: `GIT_SHA`, `IMAGE_TAG`, `BUILD_TIME`, `APP_VERSION`, `FRONTEND_BUILD_SHA`.
- `deploy/.env.candidate` is created from `deploy/.env.candidate.example` on the server and is not committed.
- nginx runtime values are rendered from `deploy/nginx/nexusdesk.edge.conf.template`; tokens are injected outside the repo.
- Current production config is backed up before any reload.

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

## Evidence To Attach To PR Or Release

- GitHub Actions run URL.
- Release image digest and `IMAGE_TAG`.
- Candidate smoke output and evidence directory.
- Release metadata consistency gate output.
- Rendered nginx diff against backup with secrets redacted.
- Final `/healthz` and `/readyz` payloads after cutover or rollback.
