# Speedaf MCP UAT Probe Runbook

## Purpose

Validate the real Speedaf MCP UAT contract before production integration. The interface document has several ambiguity points, especially content type, data wrapper format, and signature requirements. This probe must be run before enabling runtime features.

## Secret Handling

Never commit appCode, secretKey, test waybills, real caller phone numbers, or raw response payloads.

Use environment variables only:

```bash
export SPEEDAF_MCP_BASE_URL='https://uat-api.speedaf.com'
export SPEEDAF_MCP_APP_CODE='***'
export SPEEDAF_MCP_SECRET_KEY='***'
export SPEEDAF_MCP_TEST_CALLER_ID='***'
export SPEEDAF_MCP_TEST_WAYBILL_CODE='***'
export SPEEDAF_MCP_COUNTRY_CODE_DEFAULT='CH'
```

## Probe Checklist

### 1. Connectivity

- DNS resolves.
- TLS handshake succeeds.
- UAT URL reachable from the same server that will run NexusDesk.
- Speedaf whitelist permits the server egress IP.

### 2. Auth / Timestamp

- `appCode` query parameter accepted.
- `timestamp` is milliseconds.
- stale timestamp produces expected expiry error.
- invalid appCode produces expected appCode error.

### 3. Body Wrapper

Test all combinations until one is confirmed:

| Mode | Content-Type | Body |
|---|---|---|
| A | `text/plain` | `{"data":"{...}"}` |
| B | `text/plain` | `{"data":{...}}` |
| C | `application/json` | `{"data":"{...}"}` |
| D | `application/json` | `{"data":{...}}` |

Record which combination works. Default implementation should support both wrapper modes through environment switch.

### 4. Query Waybill by Caller

Endpoint:

```text
POST /open-api/mcp/order/waybillCode/query?appCode=...&timestamp=...
```

Input:

```json
{
  "callerID": "...",
  "countryCode": "CH"
}
```

Expected normalized cases:

- success with 0 rows
- success with 1 row
- success with multiple rows and `waybillCodeSuffix`
- error object

### 5. Query Order

Endpoint:

```text
POST /open-api/mcp/order/query?appCode=...&timestamp=...
```

Input:

```json
{
  "waybillCode": "...",
  "callerID": "..."
}
```

Expected fields:

- `waybillCode`
- `estimatedDeliveryTime`
- `currentBranch`
- `status`
- `orderClass`
- `acceptName`
- `acceptMobile`
- `acceptAddress`

Redaction verification:

- full `acceptMobile` must not appear in logs or prompt summary.
- full `acceptAddress` must not appear in logs or prompt summary.
- full `callerID` must not appear in logs or prompt summary.

### 6. Work Order Dry Run

Only run if Speedaf confirms UAT side effects are safe.

Endpoint:

```text
POST /open-api/mcp/workOrder/create?appCode=...&timestamp=...
```

Input:

```json
{
  "waybillCode": "...",
  "workOrderType": "WT0103-05",
  "description": "UAT delivery follow-up test from NexusDesk",
  "callerID": "..."
}
```

Must verify:

- duplicate submit behavior.
- invalid waybill behavior.
- current status not allowed behavior.

### 7. Cancel / Update Address / Voice Callback

Do not run against live-like UAT data unless Speedaf explicitly approves.

For v1 implementation, unit tests can validate payload builder and feature flag behavior without sending these actions.

## Output Template

Create a local-only report; do not commit secrets or raw payloads:

```text
Speedaf MCP UAT Probe Report
Date:
Server:
Base URL:
Egress IP:
Content-Type mode confirmed:
Data wrapper mode confirmed:
Sign required: yes/no/unknown
CountryCode CH supported: yes/no/unknown
order/query: pass/fail
waybillCode/query: pass/fail
workOrder/create dry run: skipped/pass/fail
PII redaction verification: pass/fail
Open blockers:
```

## Acceptance Gate

Runtime feature flags remain disabled until:

1. UAT contract probe passes.
2. Redaction tests pass.
3. Existing WebChat Fast Lane tests pass.
4. Speedaf confirms whitelist and appCode scope.
5. No real secret is present in repository history.
