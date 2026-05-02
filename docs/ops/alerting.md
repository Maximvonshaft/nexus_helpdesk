# NexusDesk Operational Alerting Runbook

This runbook defines the minimum alerting contract for NexusDesk production or controlled-pilot operations. The application already exposes request logging, request IDs, health endpoints, readiness checks, queue counters, and a token-gated metrics endpoint. Operations are not closed until these signals are checked or wired into monitoring.

## Mandatory probes

| Signal | Source | Severity | Action |
|---|---|---:|---|
| `/readyz` is not HTTP 200 | HTTP probe | P1 | Stop deployment, inspect DB connectivity and migration revision. |
| `/healthz` is not HTTP 200 | HTTP probe | P1 | Restart app only after reading logs. Do not blindly rebuild. |
| `external_pending_outbound > 0` while `ENABLE_OUTBOUND_DISPATCH=false` | `scripts/probe_nexus_runtime.sh` or `/api/admin/queues/summary` | P1/P2 | Confirm this is intended queued-only mode; otherwise enable provider after safety review. |
| `external_dead_outbound > 0` | Queue summary | P1/P2 | Inspect `failure_code`, `failure_reason`, provider route, and safety gate result. |
| OpenClaw event daemon heartbeat missing or stale | `/api/admin/openclaw/runtime-health` | P2 | Restart event daemon and inspect bridge connectivity. |
| dead OpenClaw sync jobs > 0 | Runtime health | P2 | Inspect job payloads and OpenClaw session availability. |
| worker logs contain repeated cycle failures | `docker compose logs worker` | P2 | Inspect DB, bridge, queue lock, and provider config. |
| disk usage above 80% | host probe | P2 | Clean logs, rotate backups, or expand disk before uploads fail. |
| uploads write probe fails | `scripts/probe_nexus_runtime.sh` | P1 | Stop accepting production attachments/POD until storage is fixed. |

## Recommended controlled-pilot command

```bash
APP_DIR=/opt/nexus_helpdesk APP_URL=http://127.0.0.1:18081 bash scripts/probe_nexus_runtime.sh
```

Exit codes:

| Code | Meaning |
|---:|---|
| `0` | Passed. |
| `1` | Failed; do not deploy or continue rollout. |
| `2` | Completed with warnings; document the accepted warnings before proceeding. |

## Metrics endpoint

`/metrics` is intentionally disabled unless `METRICS_ENABLED=true`. If enabled in production, `METRICS_TOKEN` must be set and callers must pass:

```text
X-Metrics-Token: <token>
```

Do not expose `/metrics` publicly without network restrictions and token protection.

## Outbound-specific incident triage

When a user says a message was sent but the customer did not receive it, do not stop at the API response. Check final delivery state:

```sql
select id, ticket_id, channel, status, provider_status, failure_code, failure_reason, sent_at
from ticket_outbound_messages
order by id desc
limit 50;
```

Interpretation:

| State | Meaning |
|---|---|
| `pending` | Queued only, not provider-confirmed. |
| `processing` | Claimed by worker. |
| `sent` + external channel | Provider path reported sent. |
| `sent` + `web_chat` | Local WebChat delivery only. |
| `draft` + safety provider status | Human review required. |
| `dead` | Dispatch failed or blocked. |

## Deployment acceptance evidence

Each production rollout should attach:

1. Git SHA and image tag from `/healthz`.
2. Migration revision from `/readyz`.
3. Output from `scripts/probe_nexus_runtime.sh`.
4. CI run link for `backend-ci` and `frontend-ci`.
5. Explicit statement that outbound is either `disabled/queued-only` or `enabled/openclaw`.
