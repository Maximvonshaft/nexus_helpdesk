# Speedaf UAT Smoke GitHub Workflow

## Purpose

This workflow gives operators a controlled, manual, read-only UAT smoke path for Speedaf MCP.

It validates:

- UAT base URL reachability.
- appCode acceptance.
- millisecond timestamp behavior.
- Content-Type and data wrapper compatibility mode.
- `order/waybillCode/query` with callerID and countryCode.
- `order/query` with waybillCode and callerID.
- Sanitized report generation.

It does not validate or execute write actions:

- No `workOrder/create`.
- No `order/updateAddress`.
- No `order/cancel`.
- No `callData/voice/callBack`.

## Required GitHub Settings

Configure repository secrets:

- `SPEEDAF_UAT_APP_CODE`
- `SPEEDAF_UAT_SECRET_KEY`

Configure repository variables:

- `SPEEDAF_UAT_TEST_CALLER_ID`
- `SPEEDAF_UAT_TEST_WAYBILL_CODE`

Do not paste these values into PRs, issues, comments, screenshots, or committed files.

## Workflow

Manual workflow name:

```text
speedaf-uat-smoke
```

Inputs:

- `base_url`, default `https://uat-api.speedaf.com`
- `country_code`, default `CH`
- `content_type`, one of `text/plain`, `application/json`
- `data_mode`, one of `string`, `object`
- `timeout_seconds`, default `8`

## Compatibility Matrix

Run these combinations if the first mode fails:

| Mode | content_type | data_mode |
|---|---|---|
| A | `text/plain` | `string` |
| B | `text/plain` | `object` |
| C | `application/json` | `string` |
| D | `application/json` | `object` |

## Artifact

The workflow uploads a sanitized report artifact:

```text
speedaf-uat-smoke-report
```

The report must not include appCode, secretKey, full caller phone, full waybill, full address, or raw PII.

## Pass Criteria

A read-only UAT pass requires a sanitized report with:

```text
base_url:
content_type mode that worked:
data_mode that worked:
sign required: yes/no/unknown
countryCode CH supported: yes/no/unknown
waybillCode/query: pass/fail + sanitized error code if failed
order/query: pass/fail + sanitized error code if failed
open blocker:
```

## Production Gate Boundary

Passing this workflow only supports read-only Speedaf UAT readiness.

It does not authorize:

- `SPEEDAF_WORK_ORDER_CREATE_ENABLED=true`
- `SPEEDAF_UPDATE_ADDRESS_ENABLED=true`
- `SPEEDAF_CANCEL_ENABLED=true`

Write-action flags still require separate operational approval, staging smoke, UI confirmation, and audit verification.
