# Codex App-Server Staging Validation Runbook

This runbook is the required gate before any production Codex app-server canary.

It follows issue #161 and assumes #160 has already been merged and deployed with Codex code present but disabled in production.

## Current production baseline

```text
Production provider: openclaw_responses
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=false
CODEX_APP_SERVER_CANARY_PERCENT=0
Production image: nexusdesk/helpdesk:main-88c751e
Production SHA: 88c751e2b2cae800ff1b7880094b3cf3261efbad
```

## Non-negotiable safety boundary

The Codex app-server integration is reply-only at this stage.

It must not:

- enable production traffic before staging gates pass;
- scrape browser sessions, cookies, ChatGPT UI state, local shell output, or operator credentials;
- execute shell commands from model output;
- mutate tickets, refunds, claims, addresses, shipment states, or customer-visible outbound messages;
- expose customer text, tokens, cookies, authorization headers, or upstream raw payloads in logs;
- bypass Nexus strict parser, provider router, feature flags, canary controls, or OpenClaw fallback.

## Required staging topology

Use an isolated staging deployment or a staging-only local compose stack.

Minimum topology:

```text
Nexus staging app
  -> Codex provider runtime adapter
  -> private Codex app-server endpoint
```

The Codex endpoint must be reachable only through an explicitly allowed private path, for example loopback, private subnet, VPN, Tailscale, or an equivalent restricted network. Public unrestricted access is not acceptable.

## Required staging configuration

### Stage 0: control baseline

Start with Codex disabled even in staging:

```text
WEBCHAT_FAST_AI_PROVIDER=openclaw_responses
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=false
CODEX_APP_SERVER_CANARY_PERCENT=0
```

Expected result:

```text
provider=openclaw_responses
codex_app_server_enabled=False
codex_app_server_canary_percent=0
OpenClaw reply path works
```

### Stage 1: private endpoint discovery only

Document the private Codex app-server contract without routing customer traffic:

```text
CODEX_APP_SERVER_BRIDGE_URL=<private endpoint>
CODEX_APP_SERVER_TOKEN_FILE=<secret file path>
```

Required evidence:

```text
endpoint_scheme=<http|https>
endpoint_host_class=<loopback|private_subnet|vpn|tailnet>
auth_method=<bearer|shared_header|mTLS|other>
protocol_path=</reply|/turn|other>
request_timeout_ms=<value>
total_timeout_ms=<value>
```

Do not print secret values.

### Stage 2: staging-only provider enablement

Only after Stage 1 is documented:

```text
WEBCHAT_FAST_AI_PROVIDER=codex_app_server
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true
CODEX_APP_SERVER_CANARY_PERCENT=0
```

Expected result:

```text
Provider runtime status reports Codex configured
Canary remains 0
No production traffic is affected
```

## Contract probe

Use a sanitized request. Do not include real customer PII.

Example request envelope:

```json
{
  "request_id": "staging-contract-001",
  "tenant_key": "default",
  "channel_key": "website",
  "session_id": "staging-session-001",
  "body": "Where is my parcel?",
  "recent_context": [],
  "tracking_fact_summary": null,
  "tracking_fact_evidence_present": false,
  "strict_schema": "speedaf_webchat_fast_reply_v1"
}
```

Required strict response shape:

```json
{
  "reply": "Please share your tracking number so I can check your parcel status.",
  "intent": "tracking_missing_number",
  "tracking_number": null,
  "handoff_required": false,
  "handoff_reason": null,
  "recommended_agent_action": null
}
```

Strict keys:

```text
reply
intent
tracking_number
handoff_required
handoff_reason
recommended_agent_action
```

No extra customer-visible action fields are accepted at this gate.

## Failure-path matrix

All failure cases must fail closed and preserve OpenClaw fallback.

| Case | Required behavior |
| --- | --- |
| Missing auth token | Reject request; no upstream call with empty auth |
| Invalid auth token | Reject request; no secret leak in logs |
| DNS/connect timeout | Fail closed; fallback remains available |
| Read timeout | Fail closed; fallback remains available |
| HTTP 4xx | Return controlled provider error; no raw upstream body to customer |
| HTTP 5xx | Return controlled provider error; no raw upstream body to customer |
| Invalid JSON | Strict parser rejects |
| Missing strict key | Strict parser rejects |
| Extra unsafe action key | Strict parser rejects or drops according to documented policy |
| Handoff true without reason | Strict parser rejects |
| Oversized reply | Strict parser rejects or truncates only by explicit safe policy |
| Customer PII in logs | Fail gate |
| Secret/header in logs | Fail gate |

## Required evidence package

Every staging run must produce an evidence directory with:

```text
README.md
provider_runtime_status_sanitized.json
contract_probe_request_sanitized.json
contract_probe_response_sanitized.json
failure_matrix.tsv
app_logs_tail_sanitized.txt
nginx_or_proxy_logs_tail_sanitized.txt
final_verdict.txt
```

The final verdict file must contain exactly one of:

```text
CODEX_APP_SERVER_STAGING_GATE=PASS
CODEX_APP_SERVER_STAGING_GATE=FAIL
```

## Production promotion rule

Production Codex canary is blocked unless all conditions are true:

```text
CODEX_APP_SERVER_STAGING_GATE=PASS
OpenClaw fallback verified
No secret leak
No customer PII leak
Strict parser verified
Failure matrix passed
Owner approval recorded in GitHub issue #161
```

Only then may a separate production canary PR or deployment plan be opened.

Initial production canary must be conservative:

```text
WEBCHAT_FAST_AI_PROVIDER=openclaw_responses
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true
CODEX_APP_SERVER_CANARY_PERCENT=1
```

If any canary issue appears, rollback target is:

```text
WEBCHAT_FAST_AI_PROVIDER=openclaw_responses
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=false
CODEX_APP_SERVER_CANARY_PERCENT=0
```

## Final sign-off checklist

- [ ] Private endpoint documented.
- [ ] Auth method documented without secret values.
- [ ] Staging topology documented.
- [ ] Contract probe passed.
- [ ] Failure matrix passed.
- [ ] Logs sanitized and reviewed.
- [ ] OpenClaw fallback verified.
- [ ] Production config unchanged.
- [ ] Issue #161 updated with evidence summary.
- [ ] Explicit owner approval recorded before production canary.
