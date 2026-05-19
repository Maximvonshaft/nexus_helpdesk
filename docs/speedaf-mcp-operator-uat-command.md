# Speedaf MCP Operator UAT Command

This runbook is for the operator who has the Speedaf UAT appCode/secretKey/test waybill/test caller phone. Do not paste secrets into GitHub comments, PR descriptions, screenshots, logs, or documents committed to the repository.

## What this validates

- Nexus environment can reach Speedaf UAT.
- appCode is accepted.
- timestamp is millisecond precision.
- selected Content-Type and data wrapper mode work.
- `waybillCode/query` works with `callerID + countryCode` when a test caller is supplied.
- `order/query` works with `waybillCode + callerID` when a test waybill is supplied.
- generated smoke report does not leak appCode/secretKey.

## Copy-paste command

Run from repository root on the deployment-like server. Replace placeholders locally only.

```bash
set -euo pipefail

export SPEEDAF_MCP_BASE_URL='https://uat-api.speedaf.com'
export SPEEDAF_MCP_APP_CODE='PASTE_UAT_APP_CODE_HERE'
export SPEEDAF_MCP_SECRET_KEY='PASTE_UAT_SECRET_KEY_HERE'
export SPEEDAF_MCP_COUNTRY_CODE_DEFAULT='CH'
export SPEEDAF_MCP_CONTENT_TYPE='text/plain'
export SPEEDAF_MCP_DATA_MODE='string'
export SPEEDAF_MCP_TIMEOUT_SECONDS='8'
export SPEEDAF_MCP_TEST_CALLER_ID='PASTE_TEST_CALLER_PHONE_HERE'
export SPEEDAF_MCP_TEST_WAYBILL_CODE='PASTE_TEST_WAYBILL_CODE_HERE'

bash scripts/smoke/smoke_speedaf_mcp_contract.sh
```

## If smoke fails

Try the documented compatibility matrix one mode at a time:

```bash
# Mode A: documented default
export SPEEDAF_MCP_CONTENT_TYPE='text/plain'
export SPEEDAF_MCP_DATA_MODE='string'
bash scripts/smoke/smoke_speedaf_mcp_contract.sh

# Mode B
export SPEEDAF_MCP_CONTENT_TYPE='text/plain'
export SPEEDAF_MCP_DATA_MODE='object'
bash scripts/smoke/smoke_speedaf_mcp_contract.sh

# Mode C
export SPEEDAF_MCP_CONTENT_TYPE='application/json'
export SPEEDAF_MCP_DATA_MODE='string'
bash scripts/smoke/smoke_speedaf_mcp_contract.sh

# Mode D
export SPEEDAF_MCP_CONTENT_TYPE='application/json'
export SPEEDAF_MCP_DATA_MODE='object'
bash scripts/smoke/smoke_speedaf_mcp_contract.sh
```

## Report location

Default report path:

```text
/tmp/nexus-speedaf-mcp-smoke/speedaf_mcp_contract_report.txt
```

Before sharing the report, manually confirm it does not contain appCode, secretKey, full caller phone, or full address.

## Required result to unblock runtime enablement

Send back only the sanitized facts:

```text
base_url:
content_type mode that worked:
data_mode that worked:
sign required: yes/no/unknown
countryCode CH supported: yes/no/unknown
waybillCode/query: pass/fail + sanitized error code if failed
order/query: pass/fail + sanitized error code if failed
workOrder/create UAT approved by Speedaf: yes/no
open blocker:
```

Do not send the appCode/secretKey in chat.
