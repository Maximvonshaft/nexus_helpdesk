# Speedaf Readonly UAT Probe Runbook

## Purpose

This runbook validates the Speedaf MCP read-only integration before any production feature flags are enabled.

The probe covers:

- environment configuration
- appCode presence without exposing it
- base URL reachability through the backend client
- `order/query` when a UAT waybill is provided
- `order/waybillCode/query` when a UAT callerID is provided
- redacted report generation

It does not cover and must not trigger:

- `workOrder/create`
- `order/updateAddress`
- `order/cancel`
- `callData/voice/callBack`

## Required GitHub Secrets

Set these in the repository or environment secrets before running the manual workflow:

- `SPEEDAF_UAT_MCP_ENABLED` = `true`
- `SPEEDAF_UAT_MCP_BASE_URL`
- `SPEEDAF_UAT_MCP_APP_CODE`
- `SPEEDAF_UAT_MCP_SECRET_KEY`

Optional GitHub variables:

- `SPEEDAF_UAT_MCP_CONTENT_TYPE` default `text/plain`
- `SPEEDAF_UAT_MCP_DATA_MODE` default `string`
- `SPEEDAF_UAT_MCP_REQUIRE_SIGN` default `false`

Important: if Speedaf requires a signature algorithm, keep `SPEEDAF_UAT_MCP_REQUIRE_SIGN=true`. The current backend intentionally fails closed with `sign_rule_not_configured` until the exact sign algorithm is provided and implemented. Do not guess the sign algorithm.

## Manual Workflow

Run GitHub Actions workflow:

```text
speedaf-readonly-uat-probe
```

Inputs:

- `waybill_code`: optional UAT waybill for `order/query`
- `caller_id`: optional UAT callerID for `waybillCode/query`
- `country_code`: default `CH`
- `strict`: recommended `true`

The workflow uploads:

```text
speedaf-readonly-uat-report.json
```

The report is redacted. It must not expose appCode, secretKey, callerID, or full waybillCode.

## Local Command

From repository root:

```bash
cd backend/..
PYTHONPATH=backend \
SPEEDAF_MCP_ENABLED=true \
SPEEDAF_MCP_BASE_URL='https://uat-api.speedaf.com' \
SPEEDAF_MCP_APP_CODE='PASTE_UAT_APP_CODE_HERE' \
SPEEDAF_MCP_SECRET_KEY='PASTE_UAT_SECRET_KEY_HERE' \
SPEEDAF_WORK_ORDER_CREATE_ENABLED=false \
SPEEDAF_UPDATE_ADDRESS_ENABLED=false \
SPEEDAF_CANCEL_ENABLED=false \
SPEEDAF_VOICE_CALLBACK_ENABLED=false \
python backend/scripts/speedaf_readonly_uat_probe.py \
  --waybill-code 'PASTE_UAT_WAYBILL_HERE' \
  --caller-id 'PASTE_UAT_CALLER_ID_HERE' \
  --country-code CH \
  --strict \
  --output-json /tmp/speedaf-readonly-uat-report.json
```

Do not paste real values into committed files.

## Pass Criteria

Minimum pass for read-only staging enablement:

- write-flag guard passes
- configuration check passes
- `order/query` succeeds for a known UAT waybill, or failure is confirmed as expected by Speedaf UAT data availability
- `waybillCode/query` succeeds for a known UAT callerID, or failure is confirmed as expected by Speedaf UAT data availability
- report is redacted
- no write-action endpoint is called

## Failure Interpretation

| Failure | Meaning | Action |
|---|---|---|
| `speedaf_mcp_not_configured` | Missing or disabled UAT credentials | Set GitHub secrets/vars correctly |
| `sign_rule_not_configured` | Speedaf requires signing but algorithm is not implemented | Request exact sign algorithm from Speedaf; do not guess |
| `timeout` | Network, whitelist, or endpoint latency problem | Check IP whitelist, base URL, timeout |
| `http_error` | HTTP transport failure | Check URL, TLS, whitelist, proxy |
| Speedaf API error code | Upstream rejected request | Confirm appCode, timestamp, data wrapper, callerID/waybill sample |

## Production Boundary

Passing this probe only authorizes discussion of read-only staging rollout.

It does not authorize enabling:

- `SPEEDAF_WORK_ORDER_CREATE_ENABLED`
- `SPEEDAF_UPDATE_ADDRESS_ENABLED`
- `SPEEDAF_CANCEL_ENABLED`
- `SPEEDAF_VOICE_CALLBACK_ENABLED`

Those require separate operational approval and staging smoke.