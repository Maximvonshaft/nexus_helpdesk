# PR-A Report: Speedaf Read Tracking Full Value

## Scope

This branch improves the read-only Speedaf tracking value path for WebChat Fast Lane.

Included:
- `order/query` existing adapter path
- `order/waybillCode/query` caller-based candidate lookup path
- WebChat non-stream candidate-selection response
- WebChat stream candidate-selection response

Excluded:
- Speedaf write-action enablement
- `workOrder/create` operator flow
- `order/updateAddress` operator flow
- `callData/voice/callBack`
- real Speedaf credentials or production flag changes

## Value Path

When a customer asks about a shipment:

1. If a tracking number exists, the system can call `order/query`.
2. If no tracking number exists but callerID is present, the system calls `waybillCode/query`.
3. If one candidate is returned, the system automatically resolves it through `order/query`.
4. If multiple candidates are returned, the system does not call AI, does not create a Ticket, and asks the customer to confirm the last four digits.
5. Candidate metadata exposes only suffix/hash, never the full waybill code.

## Safety Boundary

- Read-only Speedaf tools only.
- No write-action feature flag is enabled.
- Multiple-candidate response is server-generated and bypasses AI to avoid exposing full identifiers or inventing status.
- Candidate payloads contain only `waybill_suffix` and `waybill_hash`.

## Files

- `backend/app/services/tracking_fact_schema.py`
- `backend/app/services/speedaf/tracking_fact_source.py`
- `backend/app/services/tracking_fact_service.py`
- `backend/app/api/webchat_fast.py`
- `backend/tests/test_speedaf_tracking_full_value.py`
- `docs/speedaf-mcp-full-closure/PR_A_READ_TRACKING_REPORT.md`
- `docs/speedaf-mcp-full-closure/FINAL_COVERAGE_MATRIX.md`

## Production Status

Not production ready by itself.

Remaining gates:
- CI on PR head.
- Real Speedaf UAT.
- AppCode, whitelist, and sign-rule deployment validation.
- PR-B work order and address update controlled flow.
