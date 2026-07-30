# NexusDesk Operational Alerting Runbook

This runbook defines the minimum alerting contract for controlled-pilot and production operations. Operations are not closed until health, readiness, Worker progress, queue semantics, storage and Metrics authentication are checked or wired into monitoring.

## Mandatory probes

| Signal | Source | Severity | Action |
|---|---|---:|---|
| `/readyz` is not HTTP 200 | HTTP probe | P1 | Stop deployment; inspect database connectivity, migration identity, release metadata, storage and runtime signing. |
| `/healthz` is not HTTP 200 | HTTP probe | P1 | Read application logs before restart. Do not blindly rebuild. |
| external outbound backlog exists while dispatch is disabled | `scripts/probe_nexus_runtime.sh` or `/api/admin/queues/summary` | P1/P2 | Confirm queued-only mode is intentional; otherwise keep traffic disabled until Provider qualification is complete. |
| dead outbound work exists | Queue summary | P1/P2 | Inspect failure code, Provider route, safety gate and retry policy. |
| Worker progress is stale or service is unhealthy | `scripts/probe_nexus_runtime.sh` | P2 | Inspect `worker-outbound-controlled`, `worker-background-controlled` and `worker-webchat-ai-controlled`. |
| Worker logs contain repeated cycle failures | controlled Compose logs | P2 | Inspect database, queue lock, dispatch gate and Provider configuration. |
| unauthenticated Metrics request does not return 401 | runtime probe | P1 | Stop exposure and verify proxy ACL plus `METRICS_TOKEN`. |
| authenticated Metrics request is not HTTP 200 | runtime probe | P2 | Verify Metrics configuration and application logs. |
| disk usage above 80% | host probe | P2 | Rotate logs/backups or expand capacity before uploads fail. |
| upload write probe or backup readiness fails | runtime probe and `/readyz` | P1 | Stop accepting production attachments/POD until storage is healthy. |

## Recommended controlled-pilot command

```bash
APP_DIR=/opt/nexus_helpdesk \
APP_URL=http://127.0.0.1:18095 \
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
bash scripts/probe_nexus_runtime.sh
```

Exit codes:

| Code | Meaning |
|---:|---|
| `0` | Passed. |
| `1` | Failed; do not deploy or continue rollout. |
| `2` | Completed with warnings; document the accepted warnings before proceeding. |

## Metrics endpoint

The controlled topology requires Metrics to be enabled and token protected. Callers pass:

```text
X-Metrics-Token: <token>
```

The runtime probe verifies both negative and positive behavior: no token must be rejected, and the configured token must succeed. Keep the proxy network restrictions in place; never expose Metrics publicly on token protection alone.

## Outbound-specific incident triage

When an operator sees a technical send response but the customer did not receive the message, inspect the persisted final delivery state rather than treating the request response as completion:

```sql
select id, ticket_id, channel, status, provider_status, failure_code, failure_reason, sent_at
from ticket_outbound_messages
order by id desc
limit 50;
```

| State | Meaning |
|---|---|
| `pending` | Queued only, not Provider-confirmed. |
| `processing` | Claimed by the outbound Worker. |
| `sent` + external channel | Provider path reported sent. |
| `sent` + `web_chat` | Local WebChat delivery only. |
| `draft` + safety Provider status | Human review required. |
| `dead` | Dispatch failed or was blocked. |

## Deployment acceptance evidence

Each rollout should attach:

1. source SHA, frontend SHA and image digest from `/healthz`;
2. migration revision and readiness reason codes from `/readyz`;
3. output from `scripts/probe_nexus_runtime.sh`;
4. Canonical Acceptance run bound to the deployed source;
5. controlled preflight result;
6. explicit statement of which capabilities remain disabled and which have real E2E authorization.
