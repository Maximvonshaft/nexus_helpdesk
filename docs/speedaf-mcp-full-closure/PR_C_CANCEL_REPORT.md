# PR-C: Cancel Report

## 1. base commit SHA
_Generated from latest main_

## 2. branch name
`feat/speedaf-cancel-controlled-action-v1`

## 3. changed files
- `backend/app/main.py`
- `backend/app/enums.py`
- `backend/app/api/speedaf_cancel.py`
- `backend/app/services/speedaf/status_map.py`
- `backend/tests/test_speedaf_cancel_routes.py`

## 4. 接口覆盖矩阵
- POST `/api/tickets/{ticket_id}/speedaf/cancel-preview`
  - Query order status from MCP `order/query`
  - Generate JWT `confirmToken` bound to user, ticket, reasonCode, and waybill
  - Block terminal statuses (delivered, return delivered, exception signed)
- POST `/api/tickets/{ticket_id}/speedaf/cancel`
  - Validate JWT token, reasonCode, caller capability `tool:speedaf.order.cancel:write`
  - Call MCP `order/cancel`
  - Write `TicketEvent` and `ToolCallLog`
  - Handle rate limit / deduplication

## 5. 已拉通项
- MCP API structure mapped to internal endpoints.
- Terminal statuses mapped and enforced.
- Tool Call Audit Governance implemented for cancel action.

## 6. 未拉通项
- Real Speedaf AppCode / Secret remains in local overrides.
- This does not make NexusDesk production ready.

## 7. 安全边界
- Deduplication key: `speedaf-cancel:ticket:{ticket_id}:waybill:{hash}:reason:{reasonCode}`
- AI cannot automatically execute cancel (explicit manual confirmation via ticket endpoints).
- Capability required: `tool:speedaf.order.cancel:write`

## 8. 测试结果
- Backend Pytest coverage added and green.
- Constraints tested: capabilities, dedupe, token validation, terminal statuses.

## 9. production gate status
- Blocked: Wait for Real UAT / whitelist / appCode.
