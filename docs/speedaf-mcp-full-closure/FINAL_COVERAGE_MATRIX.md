# Speedaf MCP Full Closure Coverage Matrix

## Product Decision

`callData/voice/callBack` is excluded for now. Voice callback will require a separate WebCall session model and runtime gate.

## Coverage

| Interface | Current Status | Notes |
|---|---|---|
| `order/query` | implemented read path | Used by tracking facts and cancel preview/confirm status checks. |
| `order/waybillCode/query` | implemented WebChat read path | CallerID can resolve one shipment automatically or return safe suffix/hash candidates. |
| `workOrder/create` | implemented controlled backend path | Feature-flagged, operator-controlled, queued via BackgroundJob, only `WT0103-05` in this phase. |
| `order/updateAddress` | implemented controlled async backend path | Feature-flagged, operator-controlled, queued via BackgroundJob, submits WhatsApp/address confirmation request; does not claim address changed. |
| `order/cancel` | implemented backend path | Feature-flagged, capability-gated, preview-token-confirm flow. |
| `callData/voice/callBack` | excluded | Not part of this closure phase. |

## Global Boundaries

- No real Speedaf credentials are committed.
- Write actions remain feature-flagged off by default.
- LLMs do not directly execute Speedaf write actions.
- Frontend never calls Speedaf directly.
- Tool and ticket audit records must use redacted payloads.
- Multiple-candidate waybill results expose only suffix/hash, not full waybill codes.
- Address update is queued asynchronously; the HTTP response is not a Speedaf final success claim.

## Remaining Work

- Real Speedaf UAT and production whitelist validation.
- Operator UI wiring after backend approval.
- Voice callback remains excluded by product decision.
