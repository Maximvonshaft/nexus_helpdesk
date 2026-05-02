# NexusDesk Release Governance

This document closes the CI and branch-governance gap identified in the evidence-driven bug audit.

## Main branch rule

`main` is the production baseline. No direct push should be treated as release-ready unless the commit has green required checks and runtime deployment evidence.

## Required GitHub checks

Configure GitHub branch protection for `main` with these required status checks:

| Check | Required | Purpose |
|---|---:|---|
| `backend-ci` | Yes | Compile backend, run critical backend regressions, production settings contract, readiness validation. |
| `Frontend CI / Typecheck, lint, and build webapp` | Yes | TypeScript, ESLint, Vite build. |

Recommended branch protection settings:

```text
Require a pull request before merging: enabled
Require status checks to pass before merging: enabled
Require branches to be up to date before merging: enabled
Require conversation resolution before merging: enabled
Restrict who can push to matching branches: enabled for production maintainers only
Allow force pushes: disabled
Allow deletions: disabled
```

## Release evidence checklist

Every merge or server rollout should record:

1. PR URL and merge commit SHA.
2. `backend-ci` green run.
3. `Frontend CI` green run.
4. `/healthz` output after deployment.
5. `/readyz` output after deployment.
6. `scripts/probe_nexus_runtime.sh` output.
7. Alembic current revision.
8. Compose services and image tag.
9. Explicit outbound mode:
   - `queued-only`: `ENABLE_OUTBOUND_DISPATCH=false` or `OUTBOUND_PROVIDER=disabled`.
   - `external-send`: `ENABLE_OUTBOUND_DISPATCH=true` and `OUTBOUND_PROVIDER=openclaw`.
10. Rollback backup path.

## Outbound release gate

A release cannot claim external message delivery capability unless it proves final state, not just API acceptance:

```sql
select id, ticket_id, channel, status, provider_status, failure_code, failure_reason, sent_at
from ticket_outbound_messages
order by id desc
limit 20;
```

Accepted states:

| Mode | Passing condition |
|---|---|
| queued-only | API response says `dispatch_enabled=false`; UI/operator note states queued only. |
| external-send | Message reaches `sent` or a documented `dead`/review state after worker processing. |
| WebChat | Message is marked local-only and does not enter external pending queues. |

## Server rollout sequence

```bash
cd /opt/nexus_helpdesk

BACKUP_ROOT="/root/nexus_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_ROOT"
cp -a deploy "$BACKUP_ROOT/deploy"
cp -a Dockerfile "$BACKUP_ROOT/Dockerfile" 2>/dev/null || true
cp -a data "$BACKUP_ROOT/data" 2>/dev/null || true
git rev-parse HEAD > "$BACKUP_ROOT/git_head.txt"

export GIT_SHA="$(git rev-parse HEAD)"
export BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export IMAGE_TAG="nexusdesk/helpdesk:${GIT_SHA:0:12}"
export APP_VERSION="${GIT_SHA:0:12}"
export FRONTEND_BUILD_SHA="$GIT_SHA"

docker compose -f deploy/docker-compose.server.yml build app
docker compose -f deploy/docker-compose.server.yml up -d postgres app worker sync-daemon event-daemon
bash scripts/probe_nexus_runtime.sh
```

## Rollback sequence

```bash
cd /opt/nexus_helpdesk
PREV_SHA="$(cat /root/nexus_backup_xxx/git_head.txt)"
git checkout "$PREV_SHA"
cp -a /root/nexus_backup_xxx/deploy ./deploy
cp -a /root/nexus_backup_xxx/Dockerfile ./Dockerfile 2>/dev/null || true
docker compose -f deploy/docker-compose.server.yml build app
docker compose -f deploy/docker-compose.server.yml up -d
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```
