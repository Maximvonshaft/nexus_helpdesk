# Speedaf MCP Full Closure Coverage Matrix

## Product Decision

`callData/voice/callBack` is excluded for now. Voice callback will require a separate WebCall session model and runtime gate.

## Coverage

| Interface | Current Status | Notes |
|---|---|---|
| `order/query` | existing read adapter | Used by tracking facts and cancel preview/confirm status checks. |
| `order/waybillCode/query` | existing read adapter | Full WebChat candidate-selection value path remains for PR-A. |
| `workOrder/create` | existing backend job path | Full operator flow remains for PR-B. |
| `order/updateAddress` | action method exists | Controlled operator flow remains for PR-B. |
| `order/cancel` | implemented in this PR-C backend path | Feature-flagged, capability-gated, preview-token-confirm flow. |
| `callData/voice/callBack` | excluded | Not part of this closure phase. |

## Global Boundaries

- No real Speedaf credentials are committed.
- Write actions remain feature-flagged off by default.
- LLMs do not directly execute Speedaf write actions.
- Frontend never calls Speedaf directly.
- Tool and ticket audit records must use redacted payloads.

## Remaining Work

- PR-A: Speedaf read tracking full value path.
- PR-B: work order and address update controlled operator flow.
- Staging UAT and production whitelist validation.
