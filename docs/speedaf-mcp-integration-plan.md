# Speedaf MCP Core Adapter v1 Integration Plan

## Status

- Branch: `feature/speedaf-mcp-core-adapter-v1`
- Base: `main`
- Base commit at kickoff: `ceb47bf0cb4012fa0f8aff0683f55cf36f04d0a0`
- Scope: Speedaf AI customer service API integration for NexusDesk WebChat Fast Lane / future WebCall.

## Objective

Turn Speedaf MCP customer-service APIs into backend-governed NexusDesk tools. The goal is not to let the LLM call Speedaf directly. The goal is to let NexusDesk safely convert customer intent into controlled read/write business actions with audit, idempotency, PII redaction, and human handoff.

Target runtime:

```text
Customer channel
  -> NexusDesk Fast Lane
  -> intent / slot extraction / server policy
  -> Speedaf Core Adapter
  -> Speedaf MCP API
  -> trusted fact / Nexus Ticket / Speedaf work order / voice callback
  -> audit + metrics + safe customer reply
```

## Source API Capabilities

Speedaf MCP interface set:

1. `POST /open-api/mcp/order/query` — query waybill information.
2. `POST /open-api/mcp/order/waybillCode/query` — query waybill codes by caller phone and country code.
3. `POST /open-api/mcp/workOrder/create` — create a Speedaf work order.
4. `POST /open-api/mcp/order/cancel` — cancel a waybill.
5. `POST /open-api/mcp/order/updateAddress` — submit address-update / WhatsApp confirmation workflow.
6. `POST /open-api/mcp/callData/voice/callBack` — send AI voice-call session result back to Speedaf.

## Non-Negotiable Boundaries

- Frontend must never call Speedaf API directly.
- LLM must never receive appCode, secretKey, sign rules, raw Speedaf response, full phone number, or full recipient address.
- LLM must never directly execute `cancel`, `updateAddress`, or `workOrder/create`.
- All Speedaf write actions must be backend-gated, idempotent, feature-flagged, and audited.
- `order/query` and `waybillCode/query` are read-only tools.
- `workOrder/create`, `cancel`, `updateAddress`, and `voice/callBack` are write/system actions.
- Existing `/api/webchat/fast-reply` and `/api/webchat/fast-reply/stream` response contracts must remain backward compatible.

## Proposed Module Layout

```text
backend/app/services/speedaf/
  __init__.py
  client.py          # HTTP client, timestamp, data wrapper, response normalization
  schemas.py         # typed request/response dataclasses or Pydantic models
  status_map.py      # status/orderClass/reason/workOrder/actionStatus labels
  redactor.py        # PII redaction and safe summaries
  formatter.py       # Speedaf response -> Trusted Tracking Fact prompt block
  adapter.py         # business operations: query_order, query_waybill_by_phone
  action_service.py  # write actions: work order, cancel, update address, voice callback
```

## Environment Variables

```bash
SPEEDAF_MCP_ENABLED=false
SPEEDAF_MCP_BASE_URL=https://uat-api.speedaf.com
SPEEDAF_MCP_APP_CODE=
SPEEDAF_MCP_SECRET_KEY=
SPEEDAF_MCP_TIMEOUT_SECONDS=8
SPEEDAF_MCP_COUNTRY_CODE_DEFAULT=CH
SPEEDAF_MCP_CONTENT_TYPE=text/plain
SPEEDAF_MCP_DATA_MODE=string
SPEEDAF_MCP_REQUIRE_SIGN=false

WEBCHAT_TRACKING_FACT_LOOKUP_ENABLED=true
WEBCHAT_TRACKING_FACT_SOURCE=speedaf_api
WEBCHAT_TRACKING_FACT_REDACTION_ENABLED=true

SPEEDAF_WORK_ORDER_CREATE_ENABLED=false
SPEEDAF_CANCEL_ENABLED=false
SPEEDAF_UPDATE_ADDRESS_ENABLED=false
SPEEDAF_VOICE_CALLBACK_ENABLED=false
```

## Phase 0 — Contract Probe

Purpose: prove the real UAT behavior before wiring production runtime.

Probe matrix:

| Item | Probe |
|---|---|
| Auth | appCode accepted / rejected behavior |
| Timestamp | millisecond timestamp and expiry window |
| IP whitelist | verify cloud egress IP is permitted |
| Content-Type | `text/plain` vs `application/json` |
| Body wrapper | `{"data":"{...}"}` vs `{"data":{...}}` |
| Error shape | normalize `success=false` + `error` |
| Country code | confirm `CH` for Switzerland |
| Query by phone | validate `callerID + countryCode` |
| Query by waybill | validate `waybillCode + callerID` |

Output:

- `docs/speedaf-mcp-uat-probe-runbook.md`
- `scripts/smoke/smoke_speedaf_mcp_contract.sh`

## Phase 1 — Read-Only Tracking Fact MVP

### Business Flow

```text
Customer asks tracking question
  -> Extract waybillCode from message/history when available
  -> If missing waybillCode and callerID exists, call waybillCode/query
  -> If 0 result, ask customer for waybillCode
  -> If 1 result, call order/query
  -> If multiple results, ask customer to confirm waybill suffix
  -> Redact Speedaf response
  -> Convert to Trusted Tracking Fact
  -> Inject trusted fact into AI provider
  -> AI replies only using trusted fact
```

### Existing Nexus Hook

Current Fast Lane already calls tracking fact lookup before AI generation. The change should add `speedaf_api` as a second source next to `external_channel_bridge` and route through `WEBCHAT_TRACKING_FACT_SOURCE`.

### Redaction Rules

Allowed in prompt:

- waybill suffix or customer-provided waybill reference
- status code + safe status label
- orderClass label
- currentBranch if not PII-sensitive
- estimatedDeliveryTime if provided
- checked_at

Blocked from prompt/logs:

- `acceptMobile`
- full `acceptAddress`
- full `callerID`
- appCode / secretKey / sign / auth material
- raw Speedaf payload

## Phase 2 — Nexus Ticket -> Speedaf Work Order

Speedaf `workOrder/create` must be triggered only after Nexus has created or reused a Ticket.

Initial automatic work order allowlist:

| Nexus intent | Speedaf workOrderType | Enabled |
|---|---:|---|
| delivery follow-up / urge delivery | `WT0103-05` | feature-flagged |

Do not automatically create Speedaf work orders for lost parcel, damage, POD dispute, complaint, compensation, customs, or cancellation in v1.

Required job:

```text
speedaf.work_order.create
```

Required local idempotency:

```text
speedaf-workorder:ticket:{ticket_id}:WT0103-05
```

Audit targets:

- `ToolCallLog`
- `TicketEvent`
- BackgroundJob status / retry / dead state

## Phase 3 — High-Risk Actions, Backend Capability Only

### Cancel Order

- `SPEEDAF_CANCEL_ENABLED=false` by default.
- Must require explicit backend confirmation token.
- Must query order status before action.
- Must write full audit trail.
- Must never be executed directly from LLM output.

### Update Address Flow

- `SPEEDAF_UPDATE_ADDRESS_ENABLED=false` by default.
- Product wording: submit address-update / WhatsApp confirmation request.
- Never state that address has already been changed unless Speedaf provides a definitive success state for that exact operation.

## Phase 4 — Voice Callback

Prepare `speedaf.voice.callback` service/job for WebCall.

Payload builder should support:

- callSessionId
- callerNumber
- callStartTime
- callEndTime
- aiVendor
- userIntentSummary
- aiResultSummary
- isTransferredToHuman
- transferTime
- transferReason
- action

Action status values:

- `SUCCESS`
- `FAILED`

## Test Matrix

Required tests:

- Speedaf client URL and query parameter construction.
- timestamp in milliseconds.
- body wrapper modes.
- content-type modes.
- success response normalization.
- error response normalization.
- redaction blocks phone/address/secrets.
- order query result becomes trusted tracking fact.
- no trusted fact means AI cannot claim live parcel status.
- caller phone returns multiple waybills and asks suffix confirmation.
- work order job only after Nexus Ticket.
- work order job dedupes.
- cancel/updateAddress feature flags default off.
- existing WebChat Fast Lane tests still pass.

## Production Cutover

1. Run UAT probe from the production-like server egress IP.
2. Confirm Speedaf whitelist.
3. Set env variables through deployment secret mechanism only.
4. Enable read-only tracking first.
5. Run WebChat Fast Lane smoke.
6. Enable work order creation only after Ticket flow is confirmed.
7. Keep cancel/updateAddress disabled until UI confirmation and compliance sign-off.

## Rollback

Read-only rollback:

```bash
WEBCHAT_TRACKING_FACT_SOURCE=external_channel_bridge
# or
WEBCHAT_TRACKING_FACT_LOOKUP_ENABLED=false
```

Write-action rollback:

```bash
SPEEDAF_WORK_ORDER_CREATE_ENABLED=false
SPEEDAF_CANCEL_ENABLED=false
SPEEDAF_UPDATE_ADDRESS_ENABLED=false
SPEEDAF_VOICE_CALLBACK_ENABLED=false
```
