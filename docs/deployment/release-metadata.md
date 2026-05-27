# Release metadata export

NexusDesk exposes release identity through `/healthz` and `/readyz` using `backend/app/services/release_metadata.py`.

The application intentionally reads release identity from environment variables at runtime. It does not read `.git` from inside the container because the production image should not contain VCS metadata.

## Required deployment flow

Before building the production image, generate a dedicated non-secret release metadata env file:

```bash
cd /opt/nexus_helpdesk
scripts/export_release_metadata.sh deploy/.release.env
set -a
. deploy/.release.env
set +a
docker compose --env-file deploy/.env.prod --env-file deploy/.release.env -f deploy/docker-compose.server.yml build
docker compose --env-file deploy/.env.prod --env-file deploy/.release.env -f deploy/docker-compose.server.yml up -d --remove-orphans
```

`deploy/.release.env` is ignored by git. It must be regenerated for each release from the checked-out commit that is being deployed.

## Values generated

The export script writes only non-secret metadata:

- `GIT_SHA`
- `COMMIT_SHA`
- `APP_GIT_SHA`
- `FRONTEND_BUILD_SHA`
- `BUILD_TIME`
- `APP_BUILD_TIME`
- `APP_VERSION`
- `IMAGE_TAG`

The script refuses to write to paths containing `secret`, `password`, or `token` so release metadata is not mixed with runtime secrets.

## Why this exists

`deploy/.env.prod` is long-lived and may contain stale `GIT_SHA`, `IMAGE_TAG`, `APP_VERSION`, or `FRONTEND_BUILD_SHA` values. If those stale values are reused during build/deploy, `/healthz` and `/readyz` can report an old release even when the running frontend assets and backend source are new.

Use `deploy/.release.env` as the per-release override source. Keep `.env.prod` for stable production configuration, not release identity.

## Verification

After deploy:

```bash
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

The returned `git_sha`, `frontend_build_sha`, `image_tag`, `build_time`, and `app_version` should match the generated `deploy/.release.env` for the release.
