# FINAL COVERAGE MATRIX

## Overview
This matrix summarizes the implementation coverage for the Speedaf MCP full closure across reading, workorders, and cancellation.

### Exclusions
- `callData/voice/callBack` excluded by product decision.
- All write actions default to disabled via feature flags.
- Real Speedaf UAT / whitelist / appCode / sign remains a deployment gate.
- **This does not make NexusDesk production ready.**

## Action Mapping

| Action / Capability | Endpoint | Security Gate | Status |
|---------------------|----------|---------------|--------|
| Tracking / Query | `order/query`, `waybill_code/query` | Read-only | Completed |
| Workorder Create | `workOrder/create` | `tool:speedaf.work_order.create:write` | Completed |
| Address Update | `order/updateAddress` | `tool:speedaf.order.update_address:write` | Completed |
| Cancel Preview | `tickets/{id}/speedaf/cancel-preview` | `tool:speedaf.order.cancel:write` | Completed |
| Cancel Confirm | `tickets/{id}/speedaf/cancel` | Token, Dedupe, Capability | Completed |
| Voice Callback | `callData/voice/callBack` | Feature Disabled | Excluded |

## Constraints Validated
- AI cannot automatically invoke high-risk cancellation.
- Deduplication enforced on all write actions (`ToolCallLog`).
- Terminal order statuses hard-block cancellation attempts.
- Short-lived JWT tokens bound to `ticket_id`, `waybill`, `user_id`, and `reasonCode` ensure secure manual approval.
