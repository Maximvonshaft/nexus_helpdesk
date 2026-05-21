# PR-B Report: Speedaf Work Order and Address Update Controlled Flow

## Scope

This branch adds controlled backend operator paths for:

- `workOrder/create`
- `order/updateAddress`

Excluded:

- `callData/voice/callBack`
- AI-triggered address update execution
- production feature flag enablement
- real Speedaf credentials
- frontend UI wiring

## Work Order Flow

`POST /api/tickets/{ticket_id}/speedaf/work-orders`

Controls:

- Requires `SPEEDAF_WORK_ORDER_CREATE_ENABLED=true`.
- Allows only `WT0103-05` in this phase.
- Queues the existing `speedaf.work_order.create` BackgroundJob.
- Keeps the existing dedupe key: `speedaf-workorder:ticket:{ticket_id}:WT0103-05`.
- Truncates `description` to the documented 200-character contract.
- Writes a TicketEvent when queued.

## Address Update Flow

`POST /api/tickets/{ticket_id}/speedaf/address-update`

Controls:

- Requires `SPEEDAF_UPDATE_ADDRESS_ENABLED=true`.
- Requires an operator-controlled backend request with `waybillCode`, `callerID`, and `whatsAppPhone`.
- Does not let AI execute the action automatically.
- Uses a DB-backed dedupe key binding ticket, waybill hash, and WhatsApp phone hash.
- Writes a TicketEvent on success.
- The response says a confirmation request was submitted; it does not claim the address has already changed.

## Safety Boundary

- All write actions remain feature-flagged off by default.
- Frontend never calls Speedaf directly.
- LLMs do not directly execute these write actions.
- PII is redacted in audit payloads.
- `callData/voice/callBack` remains excluded.

## Tests

Focused tests cover:

- work order disabled by default
- work order enabled queues a job
- work order description is truncated to 200 characters
- address update disabled by default
- address update success message does not claim the address changed
- address update duplicate submission is blocked

## Production Status

Not production ready by itself.

Remaining gates:

- CI on PR head.
- Real Speedaf UAT.
- Operational approval before enabling write feature flags.
- Operator UI wiring after backend approval.
