# Speedaf Readonly UAT Phase 1 Report

## Decision

Readonly Speedaf `order/query` UAT Phase 1 is accepted as passed for staging rollout planning.

`order/waybillCode/query` is not removed and not treated as failed. It is deferred to Phase 2 because Speedaf has not yet provided a callerID/phone that is known to have bound UAT waybill candidates.

This is an explicit scope decision, not a silent skipped test.

## Evidence Summary

Manual workflow:

- `speedaf-readonly-uat-probe`

Confirmed behavior from the successful UAT run:

- UAT base URL was reachable.
- `SPEEDAF_MCP_ENABLED` was loaded.
- `SPEEDAF_MCP_APP_CODE` was loaded.
- `SPEEDAF_MCP_SECRET_KEY` was loaded.
- Speedaf write flags were all disabled.
- `order/query` returned a valid tracking fact for UAT waybill `CH020000008030`.
- Parsed status: `730`.
- Parsed status label: `return delivered`.
- Evidence was present.
- PII redaction was enabled.
- `waybillCode/query` returned successfully with `candidate_count=0` for the supplied callerID, which is acceptable for Phase 1 because the callerID was not confirmed by Speedaf to have bound UAT waybills.

## Why waybillCode/query is Phase 2

The `order/waybillCode/query` API is a caller-to-waybill lookup. It can only prove value if Speedaf provides a callerID that actually has one or more UAT waybills bound to it.

Using an arbitrary callerID proves only that the endpoint can return safely, not that the business lookup produces candidates.

Phase 2 acceptance requires Speedaf to provide:

- a UAT callerID/phone;
- at least two UAT waybills bound to that callerID;
- expected candidate suffixes or expected candidate count.

## Staging Rollout Scope Allowed After Phase 1

Allowed to plan:

- read-only tracking staging rollout based on direct `waybillCode + callerID` lookup.

Not allowed yet:

- production enablement;
- automatic waybill discovery from callerID;
- `workOrder/create`;
- `order/updateAddress`;
- `order/cancel`;
- `callData/voice/callBack`.

## Required Staging Conditions

Before enabling read-only tracking in staging:

1. Keep all Speedaf write feature flags disabled.
2. Keep voice callback excluded.
3. Configure only read-only Speedaf MCP variables.
4. Run staging smoke after deployment.
5. Verify `/healthz` and `/readyz`.
6. Run a real order tracking request against a known UAT waybill.
7. Verify ticket/customer logs do not expose full callerID, full waybill, appCode, or secretKey.
8. Keep rollback ready by disabling the read-only tracking source flag.

## Feature Flag Boundary

Read-only staging may only consider flags related to Speedaf tracking facts.

These remain disabled:

- `SPEEDAF_WORK_ORDER_CREATE_ENABLED=false`
- `SPEEDAF_UPDATE_ADDRESS_ENABLED=false`
- `SPEEDAF_CANCEL_ENABLED=false`
- `SPEEDAF_VOICE_CALLBACK_ENABLED=false`

## Phase 2 Acceptance Criteria

Phase 2 is complete only when `order/waybillCode/query` returns expected candidates from a Speedaf-provided callerID.

Required evidence:

- callerID source confirmed by Speedaf;
- returned candidate count is greater than zero;
- returned candidate suffixes match Speedaf expectation;
- full waybill codes remain redacted from customer-facing and operator logs;
- multi-candidate UX remains safe and requires customer/operator selection.

## Current Production Verdict

- Readonly `order/query`: PASS for staging rollout planning.
- `waybillCode/query`: DEFERRED to Phase 2 pending valid Speedaf callerID dataset.
- Write actions: NOT AUTHORIZED.
- Voice callback: EXCLUDED.
- Production rollout: NOT AUTHORIZED.
