# Codex Chat Smoke Runbook

This runbook covers the current Nexus-owned Codex app-server bridge path. The retired OpenClaw Codex harness is not part of the runtime image, compose topology, or smoke gate.

## Runtime Chain

- Public app calls the WebChat Fast provider runtime.
- Provider runtime routes Codex traffic through `codex-app-server-bridge` on port `18794`.
- The bridge talks to `codex-private-reply-engine` on port `18796` by default.
- Fallback is `rule_engine` unless explicitly configured otherwise.

## Required Checks

```bash
CODEX_PRIVATE_REPLY_ENGINE_READYZ_TIMEOUT_SECONDS=30
CODEX_APP_SERVER_PRIVATE_READYZ_TIMEOUT_SECONDS=30
CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS=30
READY_WAIT_DEADLINE_SECONDS=180
CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL=http://codex-private-reply-engine:18796/reply

wait_readyz() {
  service="$1"
  url="$2"
  deadline_seconds="${READY_WAIT_DEADLINE_SECONDS:-180}"
  started_at="$(date +%s)"
  while true; do
    if curl -fsS --max-time 35 "$url" >/dev/null; then
      echo "readyz_ok service=$service"
      return 0
    fi
    if [ "$(($(date +%s) - started_at))" -ge "$deadline_seconds" ]; then
      echo "readyz_timeout service=$service url=$url" >&2
      return 1
    fi
    sleep 2
  done
}

# Do not run the admin nonce smoke until the readiness waits pass in this order.
wait_readyz codex-private-reply-engine http://127.0.0.1:18796/readyz
wait_readyz codex-app-server-upstream http://127.0.0.1:18795/readyz
wait_readyz codex-app-server-bridge http://127.0.0.1:18794/readyz

curl -fsS http://codex-private-reply-engine:18796/readyz
curl -fsS http://codex-app-server-bridge:18794/readyz
curl -fsS http://codex-app-server-private-upstream:18795/readyz
```

Expected evidence:

- `GET http://codex-private-reply-engine:18796/readyz` succeeds.
- `18796 /readyz` is healthy before bridge cutover.
- `18795/readyz` is healthy for the private upstream proxy.
- `18794/readyz` is healthy for the public bridge sidecar.
- `SMOKE_HTTP_CODE=200`
- `nonce_echoed=True`
- `VERDICT=CODEX_AUTH_AND_CHAT_MODEL_CALL_CONNECTED`
- `canary_percent=0` is valid when Codex app-server is intentionally bypassed.
- `provider_runtime fallback remains configured`
- `rule_engine fallback`

## Rollback

Set the provider runtime routing rule back to `rule_engine` or set the Codex app-server kill switch. Do not reintroduce the retired OpenClaw harness.
