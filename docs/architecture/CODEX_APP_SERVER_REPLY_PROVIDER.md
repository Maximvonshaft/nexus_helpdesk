# Codex App-Server Reply Provider

## Purpose

This document defines the first safe bridge between NexusDesk WebChat Fast Lane and a future Codex app-server backed reply runtime.

The goal is deliberately narrow: use a private bridge as a strict JSON customer-reply generator. It is not a full Codex harness, not a tool execution surface, and not a write-action runtime.

## Target flow

```text
WebChat customer
-> /api/webchat/fast-reply
-> webchat_fast_ai_service
-> provider_router
-> codex_app_server provider
-> private local reply bridge
-> Codex app-server
-> strict JSON reply
-> existing Nexus parser and policy gate
-> customer reply or fallback
```

## Current Phase 1 scope

This PR only adds a probe and safety contract. It does not change the production default provider and does not route live traffic to Codex.

The probe validates that a future private bridge can return the exact Fast Lane JSON contract:

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

The first version must remain read-only and reply-only.

- No browser session scraping.
- No customer-triggered authorization flow.
- No frontend credential exposure.
- No model-native tool execution.
- No shell, file write, browser automation, or MCP write tools.
- No direct database writes by the model.
- No direct Ticket, outbound message, Speedaf work-order, refund, address-change, or compensation action.
- No raw upstream payload stored in customer-visible surfaces, tickets, or events.
- All accepted outputs must pass the existing Nexus strict parser.
- Any parse failure, timeout, unavailable bridge, or unsafe customer-visible text must fail closed and later fall back to `openclaw_responses` when the provider is implemented.

## Why a sidecar bridge

OpenClaw's Codex support is app-server and harness oriented. The safe Nexus path is not to treat a Codex subscription credential as a normal model API key. The safe path is to isolate the app-server interaction behind a private service and keep Nexus in control of customer-visible output, policy, and audit.

## Later implementation phases

1. `codex_app_server` provider in `backend/app/services/ai_runtime/`.
2. Private sidecar process exposing `GET /healthz`, `GET /readyz`, `GET /auth/status`, and `POST /reply`.
3. Provider fallback to `openclaw_responses`.
4. Canary percentage and kill switch.
5. Metrics for request count, parse failure, timeout, fallback, elapsed time, and unsafe-output blocks.
6. Production credential storage and rotation outside the repository.

## Non-goals

- Replacing OpenClaw Responses immediately.
- Sending all customer traffic to Codex.
- Enabling Codex-native coding tools.
- Building full agent runtime, approval bridge, or tool governance in this PR.
