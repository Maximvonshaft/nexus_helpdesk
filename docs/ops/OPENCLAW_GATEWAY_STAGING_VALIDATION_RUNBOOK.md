# Official OpenClaw Gateway Staging Validation Runbook

This runbook replaces the earlier generic Codex app-server assumption with the official OpenClaw Gateway integration path.

The target chain is:

```text
Nexus Provider Runtime
  -> private local adapter / sidecar
  -> official OpenClaw Gateway HTTP API
  -> Nexus strict reply parser
  -> OpenClaw fallback / kill switch / canary guard
```

## Why Gateway HTTP is the preferred integration surface

The official OpenClaw Gateway exposes OpenAI-compatible HTTP endpoints on the Gateway port, including:

```text
GET  /v1/models
POST /v1/chat/completions
POST /v1/responses
```

For Nexus, this is cleaner than shelling out to `openclaw agent --message ...` because:

1. HTTP is easier to isolate, timeout, authenticate, and observe.
2. Nexus can keep the provider runtime sidecar stateless.
3. The response can be converted into the existing WebChat Fast Lane strict JSON shape.
4. Shell execution can remain out of scope for Nexus.

CLI usage is still useful for operator diagnostics, but it should not be the first production integration point.

## Production baseline

Production is already deployed with Codex/OpenClaw provider runtime code present but disabled:

```text
Production image: nexusdesk/helpdesk:main-88c751e
Production SHA: 88c751e2b2cae800ff1b7880094b3cf3261efbad
WEBCHAT_FAST_AI_PROVIDER=openclaw_responses
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=false
CODEX_APP_SERVER_CANARY_PERCENT=0
```

Do not change production config while executing this staging gate.

## Required OpenClaw staging topology

Run OpenClaw Gateway in an isolated staging boundary:

```text
Nexus staging app or local probe
  -> OpenClaw Gateway on loopback/private/tailnet
  -> OpenClaw agent configured for reply-only logistics CS behavior
```

Minimum OpenClaw startup target:

```bash
openclaw gateway --port 18789 --verbose
```

Preferred bind and auth posture:

```text
gateway.bind=loopback
gateway.auth.mode=token
OPENCLAW_GATEWAY_TOKEN=<stored outside repo>
```

Acceptable access paths:

```text
127.0.0.1
10.0.0.0/8 private subnet
172.16.0.0/12 private subnet
192.168.0.0/16 private subnet
100.64.0.0/10 tailnet / CGNAT range
SSH tunnel to loopback
```

Rejected access paths:

```text
public unauthenticated endpoint
LAN bind without token auth
trusted-proxy mode without an explicit allowlist
any deployment where model/tool output can directly mutate Nexus production state
```

## Required OpenClaw agent policy

For Nexus staging, configure an isolated agent profile with no filesystem/shell/device/browser power.

Minimum policy intent:

```text
reply-only
no filesystem
no shell
no browser
no canvas
no nodes
no cron
no gateway mutation
no channel sends
no customer outbound delivery
```

Expected OpenClaw-side policy shape:

```jsonc
{
  agents: {
    list: [
      {
        id: "nexus-reply-staging",
        workspace: "~/.openclaw/workspace-nexus-reply-staging",
        sandbox: {
          mode: "all",
          scope: "agent",
          workspaceAccess: "none"
        },
        tools: {
          sessions: { visibility: "self" },
          allow: [],
          deny: [
            "read",
            "write",
            "edit",
            "apply_patch",
            "exec",
            "process",
            "browser",
            "canvas",
            "nodes",
            "cron",
            "gateway",
            "image",
            "sessions_send",
            "sessions_spawn"
          ]
        }
      }
    ]
  }
}
```

The exact OpenClaw config may differ by upstream version, but the effective outcome must be verified by `openclaw security audit --deep` and by negative tests.

## Nexus strict output contract

OpenClaw must be instructed to return only this JSON object:

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

Allowed keys only:

```text
reply
intent
tracking_number
handoff_required
handoff_reason
recommended_agent_action
```

No markdown wrapper. No tool plan. No hidden action list. No operational mutation instruction.

## Gateway probe sequence

### 1. Health and operator status

```bash
openclaw gateway status --json
openclaw security audit --deep --json
```

Required result:

```text
Gateway reachable
No critical findings affecting gateway auth, network exposure, filesystem permissions, or dangerous tool access
```

### 2. HTTP model probe

```bash
curl -fsS \
  -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" \
  http://127.0.0.1:18789/v1/models
```

Required result:

```text
HTTP 200
Contains openclaw/default or selected agent model id
```

### 3. HTTP reply probe via /v1/responses

Use sanitized fake customer text only.

```bash
curl -fsS \
  -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d @request.json \
  http://127.0.0.1:18789/v1/responses
```

`request.json` must instruct OpenClaw to output the Nexus strict JSON contract only.

### 4. Parser gate

The returned assistant text must be extracted and passed through the existing Nexus strict parser. The gate fails if parsing fails or if the output contains extra unsafe keys.

## Failure matrix

| Case | Required result |
| --- | --- |
| Gateway down | Nexus provider fails closed; OpenClaw fallback remains available |
| Missing token | Gateway rejects request |
| Invalid token | Gateway rejects request |
| Non-loopback unauthenticated bind | fail gate |
| /v1/models unavailable | fail gate |
| /v1/responses unavailable | fallback to /v1/chat/completions test, otherwise fail gate |
| Timeout | provider fails closed |
| Invalid JSON response | strict parser rejects |
| Extra unsafe keys | strict parser rejects or documented drop policy rejects customer-visible action |
| Tool call emitted | fail gate |
| Shell/file/browser/canvas/node/cron/gateway access possible | fail gate |
| Secret/customer PII appears in logs | fail gate |

## Evidence package

Each staging run must produce:

```text
README.md
openclaw_gateway_status_sanitized.json
openclaw_security_audit_sanitized.json
openclaw_models_sanitized.json
openclaw_responses_request_sanitized.json
openclaw_responses_response_sanitized.json
nexus_strict_parser_result.json
failure_matrix.tsv
logs_tail_sanitized.txt
final_verdict.txt
```

Final verdict must be exactly one of:

```text
OPENCLAW_GATEWAY_STAGING_GATE=PASS
OPENCLAW_GATEWAY_STAGING_GATE=FAIL
```

## Production promotion rule

Production canary remains blocked unless all of the following are true:

```text
OPENCLAW_GATEWAY_STAGING_GATE=PASS
OpenClaw Gateway is private and authenticated
OpenClaw agent is reply-only
No filesystem/shell/browser/device/channel-send access
Nexus strict parser passes
OpenClaw fallback remains intact
No secret or customer PII leak
Owner approval recorded in issue #161
```

Only after that may a separate production canary plan be opened.

Initial production canary target, if later approved:

```text
WEBCHAT_FAST_AI_PROVIDER=openclaw_responses
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true
CODEX_APP_SERVER_CANARY_PERCENT=1
```

Immediate rollback:

```text
WEBCHAT_FAST_AI_PROVIDER=openclaw_responses
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=false
CODEX_APP_SERVER_CANARY_PERCENT=0
```
