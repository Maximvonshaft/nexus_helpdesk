# Codex App-Server Runtime v3

Nexus Codex Runtime v3 introduces a Nexus-owned Node sidecar between the 18794 bridge and Codex app-server JSON-RPC. It replaces long-term dependence on per-request `openclaw infer model run` while preserving the existing Python 18800 runtime as rollback.

Flow:

`WebChat -> ProviderRuntimeRouter -> codex_app_server provider -> 18794 bridge -> codex-appserver-runtime:18810 -> Codex app-server stdio -> account/login/start -> thread/start -> turn/start -> notifications -> strict JSON`

Key properties:

- Request-scoped auth only. The sidecar calls `account/login/start` with `chatgptAuthTokens`.
- No local profile fallback. The app-server process runs with isolated `CODEX_HOME` and `HOME`; API-key env vars are cleared.
- Ephemeral thread per request. Session-bound threads are intentionally not part of this candidate.
- Reply-only execution. `dynamicTools=[]`, `persistExtendedHistory=false`, no tool execution, no shell, no browser.
- Notification-driven completion. `turn/start` is not terminal; `turn/completed` or non-retry `error` ends collection.
- Strict output contract. The assistant output must parse as Nexus WebChat JSON.
- Default ChatGPT Codex account model is `gpt-5.5`.

Bridge switch:

- `CODEX_APP_SERVER_RUNTIME_BACKEND=python_cli_pool` routes to `http://codex-private-model-runtime:18800/reply`.
- `CODEX_APP_SERVER_RUNTIME_BACKEND=node_appserver` routes to `http://codex-appserver-runtime:18810/reply`.

OpenClaw extraction:

This implementation references OpenClaw app-server protocol practice but does not import private OpenClaw package paths. The relevant upstream main inspected for this candidate was `4a45098a866949f8cbb790840fd7ee1533855450`.

Client cache key:

`tenant_id`, `chatgptAccountId`, `chatgptPlanType`, token fingerprint, model, and runtime start options hash are included. This prevents cross-account and cross-token client reuse.
