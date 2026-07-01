# Codex App-Server Reply Provider

## Purpose

This document defines the safe bridge between NexusDesk WebChat Fast Lane and a Codex app-server backed reply runtime.

The goal is deliberately narrow: use a private bridge as a strict JSON customer-reply generator. It is not a full Codex harness, not a tool execution surface, and not a write-action runtime.

## Target flow

```text
WebChat customer
-> /api/webchat/fast-reply
-> webchat_fast_ai_service
-> provider_runtime router
-> codex_app_server provider or configured fallback route
-> private local reply bridge
-> Codex app-server adapter or stub/upstream sidecar
-> strict JSON reply
-> existing Nexus parser and policy gate
-> customer reply or fallback
```

## Implemented layers

### PR-1: Probe

The probe validates that a configured private bridge can return the exact Fast Lane JSON contract and writes sanitized artifacts.

### PR-2: Private sidecar

The sidecar exposes:

```text
GET  /healthz
GET  /readyz
GET  /auth/status
POST /reply
```

The sidecar supports:

- `disabled`: default safe mode.
- `stub`: local contract testing only; production blocked by default.
- `upstream`: forwards to a private upstream adapter and revalidates output through the Nexus strict parser.

### PR-3: Backend provider

The backend provider is named:

```text
codex_app_server
```

It is only active when explicitly configured:

```bash
WEBCHAT_FAST_AI_PROVIDER=codex_app_server
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true
CODEX_APP_SERVER_BRIDGE_URL=http://127.0.0.1:18793/reply
CODEX_APP_SERVER_TOKEN_FILE=/run/nexus/codex_reply_bridge_shared_token
CODEX_APP_SERVER_TIMEOUT_MS=15000
```

Default production behavior now routes through Provider Runtime:

```text
WEBCHAT_FAST_AI_PROVIDER=provider_runtime
WEBCHAT_FAST_AI_FALLBACK_PROVIDER=rule_engine
```

### PR-4: Canary, kill switch, and observability

The provider router now supports controlled routing when the configured provider is `codex_app_server`:

```text
CODEX_APP_SERVER_CANARY_PERCENT=0..100
CODEX_APP_SERVER_KILL_SWITCH=true|false
```

Routing behavior:

- `CODEX_APP_SERVER_KILL_SWITCH=true` routes to the configured fallback immediately.
- `CODEX_APP_SERVER_CANARY_PERCENT=0` routes all traffic to the configured fallback.
- `CODEX_APP_SERVER_CANARY_PERCENT=1..99` uses stable hash bucket routing based on tenant/session/request.
- `CODEX_APP_SERVER_CANARY_PERCENT=100` routes all eligible traffic to `codex_app_server`.

Observability is emitted through low-cardinality log metrics:

```text
webchat_codex_app_server_metric
```

Metric payload includes:

```json
{
  "status": "route|ok|error|fallback_ok|fallback_failed",
  "route": "canary_full|canary_selected|canary_skipped_fallback|kill_switch_fallback|configured_provider",
  "elapsed_ms": 0,
  "error_code": null
}
```

## Strict reply schema

Every accepted reply must satisfy the existing WebChat Fast Lane JSON shape:

```json
{
  "reply": "customer visible reply",
  "intent": "greeting|tracking|tracking_missing_number|tracking_unresolved|complaint|address_change|handoff|other",
  "tracking_number": null,
  "handoff_required": false,
  "handoff_reason": null,
  "recommended_agent_action": null
}
```

## Hard boundaries

The first implementation remains read-only and reply-only.

- No browser session scraping.
- No customer-triggered authorization flow.
- No frontend credential exposure.
- No model-native tool execution.
- No shell, file write, browser automation, or MCP write tools.
- No direct database writes by the model.
- No direct Ticket, outbound message, Speedaf work-order, refund, address-change, or compensation action.
- No raw upstream payload stored in customer-visible surfaces, tickets, or events.
- All accepted outputs must pass the existing Nexus strict parser.
- Any parse failure, timeout, unavailable bridge, or unsafe customer-visible text must fail closed and can fall back to `openai_responses` or `rule_engine` when fallback is configured.

## Why a sidecar bridge

Codex app-server support is app-server and harness oriented. The safe Nexus path is not to treat a Codex subscription credential as a normal model API key. The safe path is to isolate the app-server interaction behind a private service and keep Nexus in control of customer-visible output, policy, and audit.

## Provider behavior

`CodexAppServerProvider` sends only a constrained request payload:

```json
{
  "request_id": "...",
  "tenant_key": "default",
  "channel_key": "website",
  "session_id": "...",
  "body": "customer message",
  "recent_context": [],
  "tracking_fact_summary": null,
  "tracking_fact_evidence_present": false,
  "strict_schema": "speedaf_webchat_fast_reply_v1"
}
```

It does not forward raw tickets, secrets, browser data, or server internals.

On success, the provider returns a normal `FastAIProviderResult` with `reply_source=codex_app_server`.

On failure, it returns safe errors such as:

```text
codex_app_server_not_configured
codex_app_server_http_error
codex_app_server_unavailable
ai_invalid_output
ai_unexpected_tool_call
```

## Production controls

In production:

- `CODEX_APP_SERVER_TOKEN` is forbidden.
- `CODEX_APP_SERVER_TOKEN_FILE` is required when provider is `codex_app_server`.
- `CODEX_APP_SERVER_BRIDGE_URL` must point to private, loopback, link-local, or tailnet/CGNAT address space.
- `CODEX_APP_SERVER_CANARY_PERCENT` must be between 0 and 100.
- If `CODEX_APP_SERVER_KILL_SWITCH=true` or `CODEX_APP_SERVER_CANARY_PERCENT<100`, configure Provider Runtime fallbacks explicitly.
- Legacy `external_channel_responses` fallback is retired and must not be used for production rollback.

## Later implementation phases

1. Real private Codex app-server adapter behind the sidecar upstream mode.
2. Production credential storage and rotation outside the repository.
3. Optional admin UI to inspect provider status without exposing secrets.
4. Optional Prometheus counters/histograms wired to the existing low-cardinality metric call sites.

## Non-goals

- Re-enabling ExternalChannel Responses as a rollback path.
- Sending all customer traffic to Codex without canary controls.
- Enabling Codex-native coding tools.
- Building full agent runtime, approval bridge, or tool governance in this PR.
