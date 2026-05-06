# NexusDesk Production Observability Baseline

This baseline turns the WebChat AI runtime, Bridge/MCP tool layer, operator queue, and cursor-pagination work into an operable production-pilot monitoring package.

## Scope

This document is intentionally a baseline, not a full observability platform. It does not introduce a new metrics backend, tracing collector, or mandatory Sentry dependency. It defines the minimum metrics, dashboards, alerts, and runbooks needed before claiming a production pilot.

## Required runtime switches

| Setting | Required for pilot | Notes |
|---|---:|---|
| `METRICS_ENABLED=true` | Yes | `/metrics` must remain token-protected. |
| `METRICS_TOKEN` | Yes | Never commit the token. Pass through deployment secret management. |
| `WEBCHAT_AI_AUTO_REPLY_MODE=safe_ack` or `safe_ai` | Pilot decision | Use `off` for rollback. |
| `TOOL_GOVERNANCE_ENFORCEMENT_MODE=audit_only` | Recommended | `enforce` must be opt-in only after dry-run review. |
| `TOOL_GOVERNANCE_BLOCK_WRITE_TOOLS=true` | Recommended | Write/external-send tools should be high-risk by default. |

## Core metric families

The application already exposes request metrics and PR #47 adds AI/tool metric primitives. Production dashboards should include these families when available:

| Metric | What it answers |
|---|---|
| `nexusdesk_http_requests_total` | HTTP volume by path/method/status. |
| `nexusdesk_http_request_duration_ms` | API latency p50/p95/p99. |
| `nexusdesk_webchat_ai_turn_total` | AI turn lifecycle events by status. |
| `nexusdesk_webchat_ai_turn_duration_ms` | AI turn total duration. |
| `nexusdesk_webchat_ai_stale_suppressed_total` | Stale reply suppression spikes. |
| `nexusdesk_webchat_ai_timeout_total` | AI runtime timeout rate. |
| `nexusdesk_tool_call_total` | Tool call count and status. |
| `nexusdesk_tool_call_elapsed_ms` | Tool latency p95/p99. |
| `nexusdesk_openclaw_bridge_elapsed_ms` | Bridge call latency. |
| `nexusdesk_background_job_wait_ms` | Queue wait time. |

## Dashboard panels

### 1. WebChat intake health

- WebChat init request rate and error rate.
- WebChat send-message request rate and error rate.
- WebChat send ack p50/p95/p99.
- Realtime-lite events request rate and error rate.
- Event endpoint wait duration and 5xx rate.

### 2. AI runtime health

- AI turns by transition status: queued, coalesced, processing, bridge_calling, completed, fallback, failed, timeout, superseded.
- AI turn duration p50/p95/p99.
- Timeout rate.
- Stale suppressed count.
- Ratio of fallback/completed.

### 3. Bridge/MCP tool health

- Tool call count by `tool_name`, `tool_type`, `status`.
- Tool latency p50/p95/p99.
- Failure and timeout rates.
- `external_send` blocked/would_block count.
- Top failing tools.

### 4. Operator queue health

- Pending operator tasks.
- Assigned operator tasks.
- Resolved/dropped/replayed tasks per hour.
- Oldest pending task age.
- OpenClaw unresolved projection count.
- WebChat handoff projection count.

### 5. Database/read-path health

- Ticket list p95.
- WebChat inbox p95.
- WebChat event poll p95.
- Background job claim latency / wait time.
- Slow query samples from database logs, if enabled.

## Alert gates

Use `ops/observability/prometheus-alerts.yml` as a starting point. Alert thresholds must be tuned with staging load data before production paging.

## 10 / 20 / 50 / 100 conversation gates

| Gate | Meaning | Required evidence |
|---|---|---|
| 10 conversations | Smoke | Basic WebChat init/send/poll success, no 5xx spike. |
| 20 conversations | Pilot gate | AI turn queue wait and Bridge p95 are measured; no stuck `ai_pending`. |
| 50 conversations | Staging load gate | Cursor endpoints and indexes validated; operator queue remains usable. |
| 100 conversations | Not committed | Requires a decision on Redis/job runner/event bus/cloud AI runtime. |

## Production-pilot go/no-go checklist

A production pilot is allowed only when all of these are true:

1. `/metrics` is enabled and token-protected.
2. WebChat send ack p95 is known from staging smoke.
3. AI turn total p95 is known from staging smoke.
4. Bridge/MCP p95 and timeout rate are known.
5. Tool call audit logs do not contain raw token/prompt/body/secret.
6. Operator queue pending count and oldest pending age are visible.
7. Stale reply suppression and AI timeout alerts are enabled.
8. Rollback switches are documented and tested:
   - `WEBCHAT_AI_AUTO_REPLY_MODE=off`
   - `METRICS_ENABLED=false`
   - `TOOL_GOVERNANCE_ENFORCEMENT_MODE=off`

## Rollback signals

Immediately roll back or disable AI auto-reply when any of these happen:

- AI timeout rate stays above threshold for 10 minutes.
- Bridge p95 latency remains above threshold for 10 minutes.
- Stale suppression spike indicates repeated race conditions.
- Tool audit shows unexpected external_send attempts.
- WebChat send ack p95 exceeds the pilot threshold.
- Operator queue oldest pending age exceeds operational SLA.

## What this baseline deliberately does not do

- It does not introduce OpenTelemetry collector deployment.
- It does not require Sentry SDK installation.
- It does not claim 100 concurrent AI conversations.
- It does not replace production DBA slow-query monitoring.
- It does not turn on tool enforcement by default.
