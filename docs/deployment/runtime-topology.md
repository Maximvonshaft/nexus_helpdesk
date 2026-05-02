# NexusDesk Production Runtime Topology

This document is the deployment contract for the server-side NexusDesk runtime. It exists to prevent drift between the GitHub repository, the server compose file, the frontend API base, Tailscale access, OpenClaw bridge integration, and persistent data.

## Standard controlled-pilot topology

```text
Browser / local webapp / OpenClaw-side tools
        |
        | HTTP API base: http://<tailscale-or-host>:18081
        v
Host port 127.0.0.1:18081
        |
        v
app container :8080
        |
        +-- postgres container :5432
        +-- worker
        +-- sync-daemon
        +-- event-daemon
        +-- ../data/uploads mounted to /app/backend/uploads
```

The optional `nginx` service is behind the `edge-nginx` compose profile. It is not the default controlled-pilot API entrypoint. This keeps the existing operational convention of using host port `18081` stable while still preserving an edge nginx profile for environments where port 80 is intentionally owned by this stack.

## Compose services

| Service | Purpose | Notes |
|---|---|---|
| `postgres` | Primary database | Internal compose service by default. Use external DB only with an explicit override and documented `DATABASE_URL`. |
| `app` | FastAPI + built SPA + WebChat static assets | Binds `127.0.0.1:18081:8080`. |
| `worker` | Background jobs and optional external outbound dispatch | Dispatch is fail-closed unless `ENABLE_OUTBOUND_DISPATCH=true` and `OUTBOUND_PROVIDER=openclaw`. |
| `sync-daemon` | OpenClaw transcript sync daemon | Keeps linked conversations current. |
| `event-daemon` | OpenClaw event-driven ingestion | Must have heartbeat visible in runtime health. |
| `nginx` | Optional edge reverse proxy | Enabled only with `--profile edge-nginx`. |

## Frontend API base contract

The webapp API client owns the `/api/...` path prefix. Therefore environment values must provide only the origin/base host:

```text
VITE_API_BASE_URL=http://<host>:18081
```

Do not configure:

```text
VITE_API_BASE_URL=http://<host>:18081/api
```

The client defensively normalizes an accidental trailing `/api`, but deployment files and docs must still follow the origin-only contract.

## Runtime identity contract

Docker builds must inject:

```text
GIT_SHA
BUILD_TIME
IMAGE_TAG
APP_VERSION
FRONTEND_BUILD_SHA
```

`/healthz` returns runtime identity. `/readyz` returns runtime identity plus database readiness and the current Alembic migration revision. A release is not evidence-driven unless the runtime SHA, image tag, frontend build SHA, and migration revision are captured in the deployment record.

## Storage contract

Strict production should use S3-compatible object storage. Controlled pilots may use local storage only if:

1. `../data/uploads` is mounted to `/app/backend/uploads` for app, worker, sync-daemon, and event-daemon.
2. Daily server backup includes `data/uploads`.
3. A restore drill has been performed before accepting customer POD/attachment evidence as production-critical.

## Outbound semantics contract

`POST /api/tickets/{id}/outbound/send` means the message was accepted into the outbound pipeline. It does not mean provider delivery succeeded.

Final state must be verified through `ticket_outbound_messages` or queue/runtime health:

| State | Meaning |
|---|---|
| `pending` / `queued` | Accepted into queue, not provider-confirmed. |
| `processing` | Claimed by worker. |
| `sent` | Provider or local WebChat delivery completed. Check `external_send` / `delivery_semantics`. |
| `dead` | Failed or blocked by dispatch/safety gate. |
| `draft` / review status | Human review is required before send. |

WebChat local ACK/card/handoff/AI replies are local-only deliveries and must not be interpreted as WhatsApp/Telegram/SMS/Email external sends.

## Release evidence checklist

Before merging or deploying a release, capture:

```bash
bash scripts/probe_nexus_runtime.sh
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
docker compose -f deploy/docker-compose.server.yml ps
docker compose -f deploy/docker-compose.server.yml exec -T app alembic current
```

A release is not closed until the probe passes or every warning is explicitly accepted in the deployment note.
