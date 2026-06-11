# Provider Runtime Governance Pre-Audit

## Git SHA

`8d9d2fc0c8d6bfcad3e5769221835eb733fd9d46`

## Files Inspected

- `deploy/.env.prod.example`
- `deploy/docker-compose.server.yml`
- `backend/app/services/provider_runtime/router.py`
- `backend/app/settings.py`
- `backend/app/api/admin.py`
- `backend/app/api/admin_outbound_semantics.py`
- `webapp/src/routes/runtime.tsx`
- `webapp/src/routes/index.tsx`
- `webapp/src/lib/api.ts`

## deploy/.env.prod.example Current Values

| Variable | Current value |
| --- | --- |
| `WEBCHAT_FAST_AI_PROVIDER` | `provider_runtime` |
| `WEBCHAT_FAST_AI_FALLBACK_PROVIDER` | `rule_engine` |
| `PROVIDER_RUNTIME_PRIMARY_PROVIDER` | `codex_app_server` |
| `PROVIDER_RUNTIME_FALLBACK_PROVIDERS` | `openclaw_responses,rule_engine` |
| `CODEX_DIRECT_ENABLED` | `false` |
| `CODEX_DIRECT_HOME` | `/app` |
| `CODEX_DIRECT_SANDBOX_ACKNOWLEDGED` | `false` |
| `OPENCLAW_BRIDGE_ENABLED` | `false` |
| `OPENCLAW_SYNC_ENABLED` | `true` (duplicated in file) |
| `OPENCLAW_EVENT_DRIVER_ENABLED` | `true` |
| `OPENCLAW_RESPONSES_URL` | `http://100.106.75.61:18792/responses` |
| `OPENCLAW_RESPONSES_STREAM_URL` | `http://100.106.75.61:18789/v1/responses` |
| `CODEX_APP_SERVER_BRIDGE_URL` | `http://172.18.0.1:18794/reply` |
| `CODEX_APP_SERVER_LOGIN_URL` | `http://172.18.0.1:18794/login` |

## deploy/docker-compose.server.yml Current Sidecar / OpenClaw Surface

Default app volume anchor currently mounts:

- `/opt/nexus_helpdesk/deploy/runtime_secrets/openclaw_responses_token:/run/openclaw_responses_token:ro`
- `/opt/nexus_helpdesk/deploy/runtime_secrets/openclaw_native_responses_token:/run/openclaw_native_responses_token:ro`
- `/opt/nexus_helpdesk/deploy/runtime_secrets/codex_app_server_bridge_token:/run/nexus/codex_app_server_bridge_token:ro`

Default app environment anchor currently includes:

- `OPENCLAW_DEPLOYMENT_MODE: remote_gateway`
- `OPENCLAW_TRANSPORT: mcp`
- `OPENCLAW_CLI_FALLBACK_ENABLED: "false"`
- `CODEX_APP_SERVER_AUTH_MODE`
- `CODEX_APP_SERVER_LEGACY_LOGIN_STATE_ENABLED`
- `CODEX_APP_SERVER_TOTAL_TIMEOUT_MS`
- `CODEX_APP_SERVER_CONNECT_TIMEOUT_MS`

Profile services currently present in default compose:

- `codex-app-server-bridge` with profile `codex-app-server`, bridge token mount, `CODEX_APP_SERVER_*` env, host port `172.18.0.1:18794:18794`.
- `codex-appserver-runtime` with profile `codex-app-server`, `CODEX_APPSERVER_*` env.
- `codex-app-server-upstream` with profile `codex-app-server`, `CODEX_APP_SERVER_PRIVATE_*` env, host port `172.18.0.1:18795:18795`.
- `codex-private-reply-engine` with profile `codex-app-server`, private model env.
- `codex-openclaw-home-permissions` with profile `codex-app-server`, OpenClaw home mount.
- `codex-private-model-runtime` with profile `codex-app-server`, OpenClaw/Codex env and OpenClaw/Codex token mounts.
- `worker-openclaw-inbound` with profile `openclaw-inbound`.
- `sync-daemon` with profile `openclaw`.
- `event-daemon` with profile `openclaw` and `OPENCLAW_EVENT_DRIVER_ENABLED: "true"`.

## Provider Runtime Router Current Defaults

When no DB routing rule exists, `backend/app/services/provider_runtime/router.py` currently sets:

- `primary_provider = "codex_app_server"`
- `fallbacks = ["openclaw_responses", "rule_engine"]`
- `output_contract = "speedaf_webchat_fast_reply_v1"`
- `timeout_ms = 10000`
- `kill_switch = False`
- `canary_percent = 100`

Current `_apply_env_overrides()` behavior:

- Applies `PROVIDER_RUNTIME_PRIMARY_PROVIDER` when present.
- Applies `PROVIDER_RUNTIME_FALLBACK_PROVIDERS` only when the env string is non-empty.
- If primary is `codex_direct` and fallback env string is empty/missing, implicitly falls back to `WEBCHAT_FAST_AI_FALLBACK_PROVIDER` with default `openai_responses,rule_engine`.
- Explicit JSON `[]` is not distinguished from an absent fallback env at the initial truthiness check.

The current hard return for `codex_direct` primary failure exists at the end of `route()`.

## Current OpenClaw Admin API / Frontend Exposure

Backend admin API exposure in `backend/app/api/admin.py`:

- `POST /api/admin/openclaw/link`
- `POST /api/admin/openclaw/tickets/{ticket_id}/sync`
- `GET /api/admin/openclaw/links`
- `POST /api/admin/openclaw/sync/enqueue`
- `POST /api/admin/openclaw/sync/enqueue-stale`
- `GET /api/admin/openclaw/runtime-health`
- `GET /api/admin/openclaw/connectivity-check`
- `POST /api/admin/openclaw/events/consume-once`
- `GET /api/admin/openclaw/unresolved-events`
- `POST /api/admin/openclaw/unresolved-events/{event_id}/replay`
- `POST /api/admin/openclaw/unresolved-events/{event_id}/drop`

Additional runtime health exposure exists in `backend/app/api/admin_outbound_semantics.py`:

- `GET /api/admin/openclaw/runtime-health`

Frontend/API client exposure:

- `webapp/src/lib/api.ts` exposes runtime health, connectivity check, consume-once, unresolved event replay/drop helpers.
- `webapp/src/routes/runtime.tsx` fetches OpenClaw connectivity by default for permitted ops users and renders OpenClaw cards/actions.
- `webapp/src/routes/index.tsx` renders OpenClaw/sync-oriented metrics and calls `consumeOpenClawEventsOnce()`.

## Explicitly Not Deleted In This Governance Pass

- OpenClaw models, migrations, schemas, transcript structures, attachment references, unresolved event structures, and historical data access fields.
- Provider runtime dispatcher, router, adapters, registry, output contracts, and WebChat Fast API.
- `backend/app/services/provider_runtime/adapters/codex_direct.py`.
- Existing worker services required for default production: `worker-outbound`, `worker-background`, `worker-handoff-snapshot`, `worker-webchat-ai`.
- Historical OpenClaw docs and scripts unless they are default production entrypoint templates.
