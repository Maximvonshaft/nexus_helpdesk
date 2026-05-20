# PR-C Report: Speedaf Cancel Controlled Action

## Scope

This branch implements the controlled backend path for Speedaf order cancellation.

Included:
- `POST /api/tickets/{ticket_id}/speedaf/cancel-preview`
- `POST /api/tickets/{ticket_id}/speedaf/cancel`

Excluded:
- `callData/voice/callBack`
- AI-triggered cancel execution
- frontend UI wiring
- production feature flag enablement

## Safety Boundary

Cancel is treated as a highest-risk write action.

Controls:
- `SPEEDAF_CANCEL_ENABLED` must be enabled before preview or confirm can run.
- Operator must have `tool:speedaf.order.cancel:write` capability.
- Request must include Speedaf customer `callerID`; internal user id is never used as `callerID`.
- `reasonCode` must be one of `CC01` through `CC05`.
- Preview performs `order/query` before issuing a confirm token.
- Confirm token is short-lived and bound to ticket id, waybill hash, caller hash, reason code, and user id.
- Confirm re-runs `order/query` immediately before calling Speedaf cancel.
- Terminal status codes `5`, `730`, and `-2` block cancellation.
- Duplicate confirm submissions are blocked by a DB-backed `speedaf_cancel_idempotency` key.
- Speedaf tool calls and ticket events store safe summaries only.

## Files

- `backend/app/api/speedaf_cancel.py`
- `backend/app/main.py`
- `backend/app/services/speedaf/status_map.py`
- `backend/alembic/versions/20260521_0027_speedaf_cancel_controlled_action.py`
- `backend/tests/test_speedaf_cancel_controlled_action.py`

## Production Status

Not production ready by itself.

Remaining gates:
- Speedaf UAT credentials through deployment secrets only.
- Speedaf IP whitelist and appCode validation.
- Operational approval before setting `SPEEDAF_CANCEL_ENABLED=true`.
- Frontend operator UI can be added after backend gate approval.
