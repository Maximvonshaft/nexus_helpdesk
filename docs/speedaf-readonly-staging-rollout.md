# Speedaf Readonly Tracking Staging Rollout Runbook

## Scope

This runbook enables staging validation for read-only Speedaf tracking facts after UAT Phase 1 passed for `order/query`.

It does not enable any Speedaf write action.

## Preconditions

- `speedaf-readonly-uat-probe` has passed for `order/query`.
- UAT waybill `CH020000008030` or another Speedaf-confirmed UAT waybill returns a valid status.
- `order/waybillCode/query` is documented as Phase 2 pending a valid callerID dataset.
- Branch protection required checks are active for `main`.
- Staging smoke workflow is available.

## Staging Environment Variables

Set read-only variables only:

```text
SPEEDAF_MCP_ENABLED=true
SPEEDAF_MCP_BASE_URL=https://uat-api.speedaf.com
SPEEDAF_MCP_APP_CODE=<staging-or-uat-appCode>
SPEEDAF_MCP_SECRET_KEY=<staging-or-uat-secretKey>
SPEEDAF_MCP_CONTENT_TYPE=text/plain
SPEEDAF_MCP_DATA_MODE=string
SPEEDAF_MCP_REQUIRE_SIGN=false
SPEEDAF_MCP_COUNTRY_CODE_DEFAULT=CH
```

Keep write variables disabled:

```text
SPEEDAF_WORK_ORDER_CREATE_ENABLED=false
SPEEDAF_UPDATE_ADDRESS_ENABLED=false
SPEEDAF_CANCEL_ENABLED=false
SPEEDAF_VOICE_CALLBACK_ENABLED=false
```

Enable Speedaf as read-only tracking source only if the deployed code supports the exact feature flag for tracking fact source selection.

Do not invent new environment variable names during operations.

## Deployment Steps

1. Deploy latest `main` to staging.
2. Run database migrations.
3. Restart app and worker services.
4. Confirm `/healthz` returns healthy.
5. Confirm `/readyz` returns ready.
6. Run staging smoke workflow against the staging base URL.
7. Run a read-only tracking request for a known UAT waybill.
8. Confirm logs are redacted.
9. Confirm no Speedaf write endpoint is called.

## Validation Checklist

- `order/query` returns a valid tracking fact.
- Status code is mapped into NexusDesk status label.
- PII is redacted.
- Full appCode and secretKey are not logged.
- Full callerID is not logged.
- Full waybillCode is not logged except where explicitly required as protected backend input.
- No TicketEvent claims an address was changed.
- No cancellation is submitted.
- No work order is created.
- Voice callback remains excluded.

## Rollback

Rollback is feature-flag based:

1. Disable the read-only Speedaf tracking source flag.
2. Restart app and worker if environment variables are read at process start.
3. Confirm tracking falls back to the previous source or safe unavailable state.
4. Keep write flags disabled.

## Phase 2 Follow-up

Ask Speedaf for a callerID/phone with known bound UAT waybills.

Phase 2 validates:

- `order/waybillCode/query` returns candidate count greater than zero;
- candidate suffixes match Speedaf expectation;
- multi-candidate selection remains safe;
- full waybill values are redacted in UI/log outputs.

## Non-Goals

- No production rollout.
- No write-action rollout.
- No voice callback rollout.
- No guessed signing implementation.
